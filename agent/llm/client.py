"""The LLM_Client: model reasoning and tool selection (Requirements 5, 6).

Wraps the OpenAI SDK pointed at the ``BASE_URL`` endpoint, authenticated by
``API_KEY``, requesting model ``MODEL`` (R6.1-R6.3). It exposes two operations
to the Agent_Loop:

* :meth:`LLMClient.decide` -- send the current Task context plus the tool
  catalog and classify the model's response:
    - a named catalog tool with parseable arguments -> ``SELECT_TOOL`` (R6.5);
    - a tool not in the catalog or unparseable arguments -> ``INVALID_RESPONSE``
      recorded through the Logger (R6.6);
    - an endpoint error or no response within 120 s -> ``FAILURE`` recorded
      through the Logger, with the Owner notified (R6.7);
    - language-model features disabled -> ``FEATURE_UNAVAILABLE`` returned
      without any network call, with the Owner notified (R6.8);
    - a textual answer with no tool call -> ``TASK_COMPLETE`` carrying the
      model's reply verbatim.
* :meth:`LLMClient.analyze` -- send a tool result back to the model and return
  the next decision (the next tool to run, or task completion) (R7.3-R7.5).

The forwarded instruction is sent verbatim and the model's reply is returned
unaltered, preserving the Owner's language (Requirements 5.1, 5.2). The system
prompt instructs the model to interpret Indonesian or English and answer in the
same language (R5.3).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional, Protocol

from agent.config import Config
from agent.models import Decision

# Maximum time, in seconds, to wait for a model response before failing (R6.7).
DEFAULT_REQUEST_TIMEOUT: float = 120.0

# The bilingual system instruction (Requirement 5.3). Sent as the first message
# on every decision so the model interprets Indonesian or English instructions
# and replies in the same language as the Owner.
SYSTEM_PROMPT: str = (
    "You are an autonomous AI DevOps and coding agent running on an Ubuntu "
    "VPS, controlled by a single Owner through Telegram. The Owner may write "
    "in Indonesian (Bahasa Indonesia) or English. Always interpret the "
    "instruction in whichever of those two languages it is written, and "
    "compose every reply in that same language: if the Owner writes in "
    "Indonesian, answer in Indonesian; if in English, answer in English.\n\n"
    "Anda adalah agen DevOps dan pemrograman otonom yang berjalan di VPS "
    "Ubuntu dan dikendalikan oleh satu Pemilik melalui Telegram. Pemilik dapat "
    "menulis dalam Bahasa Indonesia atau Bahasa Inggris. Selalu tafsirkan "
    "instruksi dalam bahasa tersebut dan balas dalam bahasa yang sama.\n\n"
    "To accomplish a task, select one tool at a time from the provided tool "
    "catalog by issuing a function/tool call with valid JSON arguments. After "
    "each tool result you will be asked to analyze it and either select the "
    "next tool or, when the task is fully complete, reply with a final text "
    "message (and no tool call) summarizing the outcome for the Owner."
)


class _SupportsLogging(Protocol):
    """Minimal Logger contract the LLM_Client depends on."""

    def log(self, severity: Any, category: str, message: str, **kwargs: Any) -> Any: ...


class _ChatCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Chat(Protocol):
    completions: _ChatCompletions


class _OpenAIClientLike(Protocol):
    """The subset of the OpenAI client the LLM_Client uses.

    Declaring it as a Protocol lets tests inject a recording/raising double in
    place of a real network client.
    """

    chat: _Chat


Notifier = Callable[[str], None]


class LLMClient:
    """Communicates with the model through an OpenAI-compatible endpoint."""

    def __init__(
        self,
        config: Config,
        *,
        logger: Optional[_SupportsLogging] = None,
        notifier: Optional[Notifier] = None,
        client: Optional[_OpenAIClientLike] = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        catalog_names: Optional[frozenset[str]] = None,
    ) -> None:
        """Create an LLM_Client.

        Args:
            config: the validated configuration; ``llm_enabled`` gates network
                use (R6.8).
            logger: optional Logger for recording invalid responses and
                failures (R6.6, R6.7).
            notifier: optional callback used to notify the Owner of a model
                request failure or that features are disabled (R6.7, R6.8).
            client: an OpenAI-compatible client. When ``None`` and features are
                enabled, a real :class:`openai.OpenAI` client is constructed
                from the configuration; when features are disabled no client is
                created and no network call is ever made.
            timeout: per-request timeout in seconds (R6.7).
            catalog_names: the set of valid tool names; defaults to the global
                catalog. Used to classify a selection as valid (R6.5, R6.6).
        """
        self._config = config
        self._logger = logger
        self._notifier = notifier
        self._timeout = timeout
        self._model = config.model
        if catalog_names is None:
            from agent.llm.catalog import tool_names

            catalog_names = tool_names()
        self._catalog_names = catalog_names

        self._client: Optional[_OpenAIClientLike]
        if not config.llm_enabled:
            # Features disabled: never construct a client or touch the network.
            self._client = None
        elif client is not None:
            self._client = client
        else:  # pragma: no cover - exercised only with a live endpoint
            from openai import OpenAI

            self._client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=timeout,
            )

        # The running transcript for the current task. ``decide`` resets it and
        # ``analyze`` appends to it so the model retains context across steps.
        self._messages: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        """Whether language-model features are enabled (R1.3, R6.8)."""
        return self._config.llm_enabled

    def set_notifier(self, notifier: Notifier) -> None:
        """Inject the Owner notifier after construction (wiring cycle)."""
        self._notifier = notifier

    # -- Decision (Requirement 6) ----------------------------------------- #

    def decide(
        self, task_context: dict[str, Any], tool_catalog: list[dict[str, Any]]
    ) -> Decision:
        """Request the first tool selection for a task (R6.4-R6.8).

        ``task_context`` must carry the Owner's ``instruction`` verbatim and may
        carry a ``history`` summary of recent context. The instruction is
        forwarded to the model unchanged (Requirement 5.1).
        """
        if not self.enabled:
            message = (
                "Language-model features are disabled because one or more of "
                "API_KEY, BASE_URL, or MODEL is not provided."
            )
            self._notify(message)
            return Decision.feature_unavailable(message)

        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        history = task_context.get("history")
        if history:
            self._messages.append(
                {"role": "system", "content": f"Recent context:\n{history}"}
            )
        instruction = task_context.get("instruction", "")
        # Forward the Owner's instruction verbatim (Requirement 5.1, Property 3).
        self._messages.append({"role": "user", "content": instruction})
        return self._request(tool_catalog)

    def analyze(self, tool_result: Any) -> Decision:
        """Send a tool result back to the model and return the next decision.

        Reports whether the task is complete or another step is needed
        (R7.3-R7.5). Returns ``FEATURE_UNAVAILABLE`` if features are disabled.
        """
        if not self.enabled:
            message = "Language-model features are disabled."
            self._notify(message)
            return Decision.feature_unavailable(message)

        payload = self._serialize_tool_result(tool_result)
        self._messages.append(
            {"role": "user", "content": f"TOOL RESULT:\n{payload}"}
        )
        # Re-render the catalog so the model may select a follow-up tool.
        from agent.llm.catalog import build_catalog

        return self._request(build_catalog())

    # -- Internal request/parse ------------------------------------------- #

    def _request(self, tool_catalog: list[dict[str, Any]]) -> Decision:
        """Call the endpoint and classify the response (R6.5-R6.7)."""
        assert self._client is not None  # guaranteed by the enabled guard
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=self._messages,
                tools=tool_catalog,
                tool_choice="auto",
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - any endpoint error -> FAILURE
            message = f"The model request failed: {exc}"
            self._log("error", message)
            self._notify(message)
            return Decision.failure(message)
        return self._classify(response)

    def _classify(self, response: Any) -> Decision:
        """Classify a model response into a :class:`Decision` (R6.5, R6.6)."""
        message = self._first_message(response)
        if message is None:
            text = "The model returned no choices."
            self._log("error", text)
            self._notify(text)
            return Decision.failure(text)

        tool_calls = getattr(message, "tool_calls", None)
        content = getattr(message, "content", None) or ""

        if tool_calls:
            call = tool_calls[0]
            function = getattr(call, "function", None)
            name = getattr(function, "name", None) if function is not None else None
            raw_args = getattr(function, "arguments", None) if function is not None else None

            if not name or name not in self._catalog_names:
                return self._invalid(f"named an unknown tool: {name!r}", content)

            parsed = self._parse_arguments(raw_args)
            if parsed is None:
                return self._invalid(
                    f"provided unparseable arguments for tool {name!r}: {raw_args!r}",
                    content,
                )
            # Record the selection in the transcript for follow-up context.
            self._messages.append(
                {
                    "role": "assistant",
                    "content": content or f"(selected tool {name})",
                }
            )
            return Decision.select_tool(name, parsed)

        # No tool call: the model produced a final textual answer. Returned
        # verbatim so the Owner receives the model's reply unaltered (R5.2).
        self._messages.append({"role": "assistant", "content": content})
        return Decision.task_complete(content)

    def _invalid(self, reason: str, content: str) -> Decision:
        """Build and log an ``INVALID_RESPONSE`` decision (R6.6)."""
        message = f"The model returned an invalid response: it {reason}."
        self._log("warning", message)
        # Keep the transcript coherent for any follow-up.
        self._messages.append(
            {"role": "assistant", "content": content or "(invalid response)"}
        )
        return Decision.invalid_response(message)

    @staticmethod
    def _first_message(response: Any) -> Any:
        """Return the first choice's message, or ``None`` when absent."""
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        return getattr(choices[0], "message", None)

    @staticmethod
    def _parse_arguments(raw_args: Any) -> Optional[dict[str, Any]]:
        """Parse tool-call arguments into a dict, or ``None`` if unparseable."""
        if raw_args is None or raw_args == "":
            return {}
        if isinstance(raw_args, dict):
            return raw_args
        if not isinstance(raw_args, str):
            return None
        try:
            parsed = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _serialize_tool_result(tool_result: Any) -> str:
        """Serialize a tool result (ToolResult or dict) to JSON text."""
        if hasattr(tool_result, "to_dict"):
            payload = tool_result.to_dict()
        elif isinstance(tool_result, dict):
            payload = tool_result
        else:
            payload = {"result": str(tool_result)}
        return json.dumps(payload, default=str, sort_keys=True)

    def _log(self, severity: str, message: str) -> None:
        """Record a diagnostic through the Logger when one is configured."""
        if self._logger is not None:
            self._logger.log(severity, "system", message)

    def _notify(self, message: str) -> None:
        """Notify the Owner of a failure/disabled state when a notifier exists."""
        if self._notifier is not None:
            self._notifier(message)
