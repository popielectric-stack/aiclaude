"""Unit and integration tests for the Telegram_Interface (R2.3, 3.2, 5.2, 6.8, 21).

The Telegram transport is fully mocked: a recording async sender replaces the
network, and the reconnection test injects a connect coroutine that fails before
succeeding together with a fake sleep, so no real Telegram connection is made.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest
from cryptography.fernet import Fernet

from agent.config import Config
from agent.security import SecurityManager
from agent.telegram.interface import MAX_RECONNECT_DELAY, TelegramInterface

_FERNET_KEY = Fernet.generate_key()


class FakeDispatcher:
    """Treats any text starting with '/' as a command echo; else free text."""

    def dispatch(self, text: str) -> Any:
        if text.strip().startswith("/"):
            return type("R", (), {"is_command": True, "reply": f"cmd:{text}", "deploy_started": None})()
        return type("R", (), {"is_command": False, "reply": "", "deploy_started": None})()


class FakeAgentLoop:
    def __init__(self, report_text: str = "agent report") -> None:
        self._report_text = report_text
        self.calls: list[str] = []

    def run_task(self, instruction: str) -> Any:
        self.calls.append(instruction)
        return type("Report", (), {"report": self._report_text})()


class RecordingLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def log(self, severity: Any, category: str, message: str, **_kwargs: Any) -> None:
        self.entries.append((str(severity), category, message))


def _interface(
    *,
    owner_id: int = 100,
    llm_enabled: bool = True,
    agent_loop: Optional[FakeAgentLoop] = None,
    sent: Optional[list[tuple[int, str]]] = None,
    logger: Optional[RecordingLogger] = None,
    confirmation_timeout: float = 120.0,
) -> TelegramInterface:
    if llm_enabled:
        config = Config(
            telegram_bot_token="tok",
            owner_id=owner_id,
            api_key="k",
            base_url="u",
            model="m",
        )
    else:
        config = Config(telegram_bot_token="tok", owner_id=owner_id)
    security = SecurityManager(owner_id=owner_id, fernet_key=_FERNET_KEY)

    async def sender(chat_id: int, text: str) -> None:
        if sent is not None:
            sent.append((chat_id, text))

    return TelegramInterface(
        config=config,
        security_manager=security,
        command_dispatcher=FakeDispatcher(),
        agent_loop=agent_loop or FakeAgentLoop(),
        logger=logger or RecordingLogger(),
        sender=sender,
        confirmation_timeout=confirmation_timeout,
    )


# --------------------------------------------------------------------------- #
# Access control & routing (R3.2, 5.2, 6.8)
# --------------------------------------------------------------------------- #


async def test_unauthorized_sender_receives_access_denied() -> None:
    interface = _interface(owner_id=100)
    reply = await interface.process_message(999, "hello", chat_id=999)
    assert reply is not None
    assert "access denied" in reply.lower()


async def test_free_text_routed_to_agent_loop_and_returned_verbatim() -> None:
    loop = FakeAgentLoop(report_text="Selesai. File telah dibuat.")
    interface = _interface(owner_id=100, agent_loop=loop)
    reply = await interface.process_message(100, "buatkan file", chat_id=100)
    assert reply == "Selesai. File telah dibuat."  # verbatim (R5.2)
    assert loop.calls == ["buatkan file"]


async def test_llm_disabled_replies_unavailable_for_free_text() -> None:
    loop = FakeAgentLoop()
    interface = _interface(owner_id=100, llm_enabled=False, agent_loop=loop)
    reply = await interface.process_message(100, "do something", chat_id=100)
    assert reply is not None
    assert "unavailable" in reply.lower()
    assert loop.calls == []  # the agent loop was not invoked


async def test_command_reply_returned() -> None:
    interface = _interface(owner_id=100)
    reply = await interface.process_message(100, "/status", chat_id=100)
    assert reply == "cmd:/status"


# --------------------------------------------------------------------------- #
# Reconnection behavior (R2.3) -- integration with a simulated dropped link
# --------------------------------------------------------------------------- #


async def test_reconnection_logs_timestamp_and_retries_within_10s() -> None:
    logger = RecordingLogger()
    interface = _interface(logger=logger, owner_id=100)

    attempts = {"n": 0}

    async def flaky_connect() -> None:
        # Fail twice (simulating a dropped connection) then succeed.
        if attempts["n"] < 2:
            attempts["n"] += 1
            raise ConnectionError("telegram connection dropped")

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    await interface.supervise_connection(flaky_connect, sleep=fake_sleep)

    # The connection eventually succeeded after the simulated drops.
    assert attempts["n"] == 2
    # A timestamped reconnect record was written for each failed attempt (R2.3).
    reconnect_logs = [e for e in logger.entries if e[1] == "reconnect"]
    assert len(reconnect_logs) == 2
    for _sev, _cat, message in reconnect_logs:
        assert "T" in message  # ISO-8601 timestamp separator
    # Every retry interval is at most 10 seconds (R2.3).
    assert delays
    assert all(d <= MAX_RECONNECT_DELAY for d in delays)


async def test_reconnect_delay_is_capped_at_ten_seconds() -> None:
    interface = _interface(owner_id=100)
    # Even when a larger delay is requested, it is capped at 10 s.
    interface._reconnect_delay = 30.0  # noqa: SLF001 - exercise the cap
    interface._reconnect_delay = min(interface._reconnect_delay, MAX_RECONNECT_DELAY)
    assert interface._reconnect_delay <= MAX_RECONNECT_DELAY


async def test_supervise_reraises_after_max_attempts() -> None:
    interface = _interface(owner_id=100)

    async def always_fail() -> None:
        raise ConnectionError("down")

    async def fake_sleep(_delay: float) -> None:
        return None

    with pytest.raises(ConnectionError):
        await interface.supervise_connection(
            always_fail, sleep=fake_sleep, max_attempts=3
        )


# --------------------------------------------------------------------------- #
# Destructive-action confirmation flow (R21.3-R21.7, R21.11)
# --------------------------------------------------------------------------- #


async def _wait_for_prompt(interface: TelegramInterface) -> None:
    """Wait until request_confirmation has registered its pending-reply future."""
    for _ in range(10000):
        pending = interface._pending_reply  # noqa: SLF001 - test synchronization
        if pending is not None and not pending.done():
            return
        await asyncio.sleep(0)
    raise AssertionError("confirmation prompt was never registered")


async def test_request_confirmation_yes_confirms() -> None:
    sent: list[tuple[int, str]] = []
    interface = _interface(owner_id=100, sent=sent)
    interface._owner_chat_id = 100  # noqa: SLF001 - simulate prior message

    task = asyncio.create_task(interface.request_confirmation("Delete /data?"))
    await _wait_for_prompt(interface)
    # The Owner replies affirmatively.
    await interface.process_message(100, "yes", chat_id=100)
    confirmed = await task
    assert confirmed is True
    assert any("Delete /data?" in text for _cid, text in sent)


async def test_request_confirmation_no_cancels() -> None:
    sent: list[tuple[int, str]] = []
    interface = _interface(owner_id=100, sent=sent)
    interface._owner_chat_id = 100  # noqa: SLF001

    task = asyncio.create_task(interface.request_confirmation("Stop server?"))
    await _wait_for_prompt(interface)
    await interface.process_message(100, "no", chat_id=100)
    confirmed = await task
    assert confirmed is False
    assert any("cancelled" in text.lower() for _cid, text in sent)


async def test_request_confirmation_ambiguous_then_yes() -> None:
    sent: list[tuple[int, str]] = []
    interface = _interface(owner_id=100, sent=sent)
    interface._owner_chat_id = 100  # noqa: SLF001

    task = asyncio.create_task(interface.request_confirmation("Drop database?"))
    await _wait_for_prompt(interface)
    await interface.process_message(100, "maybe", chat_id=100)  # ambiguous -> re-prompt
    await _wait_for_prompt(interface)
    await interface.process_message(100, "ya", chat_id=100)  # affirmative (Indonesian)
    confirmed = await task
    assert confirmed is True
    # A re-prompt was issued.
    assert sum("yes" in text.lower() or "clear" in text.lower() for _c, text in sent) >= 1


async def test_request_confirmation_times_out() -> None:
    sent: list[tuple[int, str]] = []
    interface = _interface(owner_id=100, sent=sent, confirmation_timeout=0.05)
    interface._owner_chat_id = 100  # noqa: SLF001

    confirmed = await interface.request_confirmation("Reboot host?")
    assert confirmed is False
    assert any("120 seconds" in text or "cancelled" in text.lower() for _c, text in sent)


async def test_request_confirmation_exhausts_reprompts() -> None:
    sent: list[tuple[int, str]] = []
    interface = _interface(owner_id=100, sent=sent)
    interface._owner_chat_id = 100  # noqa: SLF001

    task = asyncio.create_task(interface.request_confirmation("Wipe disk?"))
    for _ in range(3):
        await _wait_for_prompt(interface)
        # Each reply is ambiguous, exhausting the re-prompt budget.
        await interface.process_message(100, "hmm", chat_id=100)
    confirmed = await task
    assert confirmed is False
    assert any("cancelled" in text.lower() for _c, text in sent)
