"""The Telegram_Interface: connection, routing, and confirmation (Requirement 2, 3, 5, 21).

Built on ``python-telegram-bot`` (v20+, asyncio). Responsibilities:

* Maintain the Telegram Bot API connection, logging a timestamped reconnect
  record and retrying at intervals of at most 10 seconds when the connection is
  interrupted (R2.2, R2.3).
* Route every inbound message through :meth:`SecurityManager.authorize`,
  responding with an access-denied notice to unauthorized senders (R3.1, R3.2).
* Dispatch slash commands through the :class:`~agent.telegram.commands.CommandDispatcher`.
* Reply that language-model features are unavailable when they are disabled and
  a free-text instruction arrives (R1.6, R6.8).
* Forward free-text instructions to the Agent_Loop, executing the blocking
  run on a worker thread (``asyncio.to_thread``) so the event loop stays
  responsive, and send the resulting report to the Owner verbatim (R5.1, R5.2).
* Implement :meth:`request_confirmation` for destructive actions: present the
  action, await a yes/no reply within 120 seconds, and re-prompt on an
  ambiguous reply up to 2 additional times before cancelling (R21.3-R21.7,
  R21.11).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent.security import MAX_REPROMPTS, ConfirmationResponse

# Upper bound on the reconnect retry interval, in seconds (R2.3).
MAX_RECONNECT_DELAY: float = 10.0

# Default seconds to wait for an Owner confirmation reply before cancelling (R21.4).
CONFIRMATION_TIMEOUT_SECONDS: float = 120.0


class _SupportsAuthorize(Protocol):
    def authorize(self, sender_id: Optional[int]) -> bool: ...

    @staticmethod
    def access_denied_message() -> str: ...

    @staticmethod
    def interpret_confirmation(text: Optional[str]) -> ConfirmationResponse: ...


class _SupportsDispatch(Protocol):
    def dispatch(self, text: str) -> Any: ...


class _SupportsRunTask(Protocol):
    def run_task(self, instruction: str) -> Any: ...


class _SupportsLogging(Protocol):
    def log(self, severity: Any, category: str, message: str, **kwargs: Any) -> Any: ...


# An async sender: delivers ``text`` to ``chat_id``.
AsyncSender = Callable[[int, str], Awaitable[None]]


class TelegramInterface:
    """Connects the Telegram Bot API to the control layer."""

    def __init__(
        self,
        *,
        config: Any,
        security_manager: _SupportsAuthorize,
        command_dispatcher: _SupportsDispatch,
        agent_loop: _SupportsRunTask,
        logger: _SupportsLogging,
        application: Optional[Application] = None,
        sender: Optional[AsyncSender] = None,
        confirmation_timeout: float = CONFIRMATION_TIMEOUT_SECONDS,
        reconnect_delay: float = 5.0,
    ) -> None:
        self._config = config
        self._security = security_manager
        self._dispatcher = command_dispatcher
        self._agent_loop = agent_loop
        self._logger = logger
        self._application = application
        self._sender = sender
        self._confirm_timeout = confirmation_timeout
        self._reconnect_delay = min(reconnect_delay, MAX_RECONNECT_DELAY)

        # The chat id of the Owner, captured from the first authorized message;
        # used to deliver mid-task prompts and reports.
        self._owner_chat_id: Optional[int] = None
        # The event loop the interface runs on, captured in ``run``.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Future resolved by the next Owner reply while a confirmation is pending.
        self._pending_reply: Optional[asyncio.Future[str]] = None
        # Count of in-progress Tasks, surfaced to ``/status`` (R4.2).
        self._active_tasks: int = 0

    @property
    def application(self) -> Optional[Application]:
        """The underlying ``python-telegram-bot`` application, if built."""
        return self._application

    # -- Connection lifecycle (R2.2, R2.3) -------------------------------- #

    def build_application(self) -> Application:
        """Build the PTB application and register handlers.

        Construction does not contact the network; it only prepares the bot and
        its update handlers (R22.7).
        """
        if self._application is None:
            self._application = (
                ApplicationBuilder().token(self._config.telegram_bot_token).build()
            )
        self._application.add_handler(
            CommandHandler(
                [
                    "start",
                    "help",
                    "status",
                    "memory",
                    "clear",
                    "projects",
                    "servers",
                    "deploy",
                    "logs",
                ],
                self._on_update,
            )
        )
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_update)
        )
        return self._application

    async def run(self) -> None:  # pragma: no cover - exercised only live
        """Connect and run the bot, supervising reconnection (R2.2, R2.3)."""
        self._loop = asyncio.get_running_loop()
        if self._application is None:
            self.build_application()
        app = self._application
        assert app is not None

        async def connect() -> None:
            await app.initialize()
            await app.start()
            await app.updater.start_polling()

        await self.supervise_connection(connect)
        self._logger.log("info", "system", "Telegram interface connected.")

    async def supervise_connection(
        self,
        connect: Callable[[], Awaitable[None]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: Optional[int] = None,
    ) -> None:
        """Attempt ``connect``, retrying on failure at intervals <= 10 s (R2.3).

        On each failed attempt a timestamped reconnection record is written and
        the next retry is delayed by at most :data:`MAX_RECONNECT_DELAY`
        seconds. ``max_attempts`` bounds the retries for testing; when reached
        the last error is re-raised.
        """
        attempt = 0
        while True:
            try:
                await connect()
                return
            except Exception as exc:  # noqa: BLE001 - any connect error -> retry
                attempt += 1
                timestamp = datetime.now(timezone.utc).isoformat()
                self._logger.log(
                    "warning",
                    "reconnect",
                    f"{timestamp} Telegram connection interrupted "
                    f"(attempt {attempt}): {exc}; retrying within "
                    f"{self._reconnect_delay:g}s.",
                )
                if max_attempts is not None and attempt >= max_attempts:
                    raise
                await sleep(self._reconnect_delay)

    # -- Inbound update handling ------------------------------------------ #

    async def _on_update(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:  # pragma: no cover - thin PTB adapter
        """PTB handler: extract sender/text and route through the core logic."""
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or message.text is None:
            return
        chat_id = message.chat_id
        reply = await self.process_message(user.id, message.text, chat_id=chat_id)
        if reply is not None:
            await self._send(chat_id, reply)

    async def process_message(
        self, sender_id: Optional[int], text: str, *, chat_id: Optional[int] = None
    ) -> Optional[str]:
        """Process one inbound message and return the reply text (or ``None``).

        Returns ``None`` when the message is consumed internally (for example a
        reply that resolves a pending confirmation prompt).
        """
        if not self._security.authorize(sender_id):
            # Unauthorized: respond with the access-denied notice (R3.2).
            return self._security.access_denied_message()

        if chat_id is not None:
            self._owner_chat_id = chat_id
        elif self._owner_chat_id is None and sender_id is not None:
            self._owner_chat_id = sender_id

        # While a confirmation is pending, the next Owner reply resolves it
        # rather than starting a new instruction (R21.5).
        if self._pending_reply is not None and not self._pending_reply.done():
            self._pending_reply.set_result(text)
            return None

        result = self._dispatcher.dispatch(text)
        if getattr(result, "is_command", False):
            return result.reply

        # Free-text instruction.
        if not getattr(self._config, "llm_enabled", False):
            # Language-model features disabled: reply that they are unavailable
            # (R1.6, R6.8).
            return (
                "Language-model features are unavailable because one or more of "
                "the API_KEY, BASE_URL, or MODEL configuration variables is not "
                "provided. Slash commands remain available."
            )

        # Offload the blocking agent loop to a worker thread so the event loop
        # stays responsive (design Process & Concurrency Model).
        self._active_tasks += 1
        try:
            report = await asyncio.to_thread(self._agent_loop.run_task, text)
        finally:
            self._active_tasks -= 1
        # Send the report verbatim (R5.2).
        return getattr(report, "report", str(report))

    # -- Deployment scheduling (R4.7) ------------------------------------- #

    def in_progress_count(self) -> int:
        """Return the number of in-progress Tasks (R4.2)."""
        return self._active_tasks

    def start_deployment(self, project: Any) -> str:
        """Begin a deployment Task for ``project`` and return an acknowledgement.

        The deployment runs as a background task so the command handler returns
        promptly; its report is delivered to the Owner when it finishes (R4.7).
        """
        identifier = getattr(project, "identifier", str(project))
        name = getattr(project, "name", identifier)
        path = getattr(project, "path", "")
        instruction = (
            f"Deploy the project '{name}' (identifier {identifier}) located at "
            f"{path}."
        )
        if self._loop is not None:
            self._loop.create_task(self._run_and_report(instruction))
        return f"Deployment of project {identifier!r} has started."

    async def _run_and_report(self, instruction: str) -> None:
        """Run a Task in a worker thread and deliver its report to the Owner."""
        self._active_tasks += 1
        try:
            report = await asyncio.to_thread(self._agent_loop.run_task, instruction)
        finally:
            self._active_tasks -= 1
        await self._send_owner(getattr(report, "report", str(report)))

    # -- Destructive-action confirmation (R21.3-R21.7, R21.11) ------------ #

    def confirm_blocking(self, description: str) -> bool:
        """Synchronous confirmation gate for the Agent_Loop worker thread.

        Schedules :meth:`request_confirmation` on the interface event loop and
        blocks the calling worker thread until the Owner responds, times out, or
        the re-prompts are exhausted. Returns ``True`` only on an affirmative
        confirmation (R21.7).
        """
        if self._loop is None:
            # No running event loop (e.g. confirmation requested before the
            # interface started): deny so a destructive action is never run
            # unconfirmed.
            return False
        future = asyncio.run_coroutine_threadsafe(
            self.request_confirmation(description), self._loop
        )
        try:
            return future.result(timeout=self._confirm_timeout * (MAX_REPROMPTS + 1) + 30)
        except Exception:  # noqa: BLE001 - any failure denies the action
            return False

    async def request_confirmation(self, description: str) -> bool:
        """Present a destructive action and collect a yes/no reply (R21.3-R21.11).

        Waits up to 120 seconds for each reply; an ambiguous reply triggers a
        re-prompt up to ``MAX_REPROMPTS`` additional times, after which the
        action is cancelled and the cancellation reported (R21.4, R21.5,
        R21.11).
        """
        for attempt in range(1 + MAX_REPROMPTS):
            prompt = description
            if attempt > 0:
                prompt = (
                    f"Please reply with a clear 'yes' or 'no'. {description}"
                )
            await self._send_owner(prompt)

            loop = asyncio.get_running_loop()
            self._pending_reply = loop.create_future()
            try:
                reply = await asyncio.wait_for(
                    self._pending_reply, timeout=self._confirm_timeout
                )
            except asyncio.TimeoutError:
                await self._send_owner(
                    "No response within 120 seconds; the destructive action was "
                    "cancelled."
                )
                return False
            finally:
                self._pending_reply = None

            response = self._security.interpret_confirmation(reply)
            if response is ConfirmationResponse.YES:
                return True
            if response is ConfirmationResponse.NO:
                await self._send_owner("The destructive action was cancelled.")
                return False
            # Ambiguous/contradictory: loop to re-prompt.

        await self._send_owner(
            "No clear yes/no response was received after re-prompting; the "
            "destructive action was cancelled."
        )
        return False

    # -- Sending ---------------------------------------------------------- #

    async def _send_owner(self, text: str) -> None:
        """Send ``text`` to the Owner's chat when it is known."""
        if self._owner_chat_id is not None:
            await self._send(self._owner_chat_id, text)

    async def _send(self, chat_id: int, text: str) -> None:
        """Deliver ``text`` to ``chat_id`` (verbatim) (R5.2)."""
        if self._sender is not None:
            await self._sender(chat_id, text)
            return
        if self._application is not None:  # pragma: no cover - live path
            await self._application.bot.send_message(chat_id=chat_id, text=text)

    def notify_owner(self, text: str) -> None:
        """Thread-safe Owner notification used as the Agent_Loop notifier.

        Schedules a verbatim send on the interface event loop; safe to call from
        the worker thread running the Agent_Loop.
        """
        if self._loop is None or self._owner_chat_id is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._send(self._owner_chat_id, text), self._loop
        )
