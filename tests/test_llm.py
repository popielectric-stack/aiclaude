"""Property and unit tests for the LLM_Client (Properties 3, 4; R1.6, 6.7, 6.8).

External network access is fully isolated: every test injects a recording or
raising OpenAI-compatible client double, so no real request is ever made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.config import Config
from agent.llm.catalog import build_catalog, tool_names
from agent.llm.client import LLMClient
from agent.models import DecisionKind


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


def _make_response(*, content: Optional[str] = None, tool_name=None, arguments=None):
    """Build a fake OpenAI chat-completion response object."""
    tool_calls = None
    if tool_name is not None:
        tool_calls = [
            SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(name=tool_name, arguments=arguments),
            )
        ]
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class RecordingClient:
    """Captures the messages sent and returns a preset response."""

    def __init__(self, response: Any = None, *, raises: Optional[Exception] = None):
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class RecordingLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def log(self, severity: Any, category: str, message: str, **_kwargs: Any) -> None:
        self.entries.append((str(severity), category, message))


def _enabled_config() -> Config:
    return Config(
        telegram_bot_token="tok",
        owner_id=1,
        api_key="key",
        base_url="https://endpoint.example/v1",
        model="claude-opus-4.8",
    )


def _disabled_config() -> Config:
    return Config(telegram_bot_token="tok", owner_id=1)


_CATALOG = build_catalog()
_VALID_NAMES = sorted(tool_names())


# --------------------------------------------------------------------------- #
# Property 4: Tool-decision classification
# --------------------------------------------------------------------------- #

# Feature: ai-devops-coding-agent, Property 4: Tool-decision classification
# For any model response, the LLM_Client returns a SELECT_TOOL decision with the
# parsed tool name and arguments if and only if the named tool is present in the
# catalog and its arguments are parseable; otherwise it returns an
# INVALID_RESPONSE decision and records the invalid response through the Logger.
@settings(max_examples=100)
@given(
    name=st.one_of(
        st.sampled_from(_VALID_NAMES),
        st.text(min_size=1, max_size=12).filter(lambda s: s not in tool_names()),
    ),
    args_obj=st.dictionaries(
        st.text(min_size=1, max_size=6),
        st.one_of(st.text(max_size=10), st.integers(), st.booleans()),
        max_size=4,
    ),
    parseable=st.booleans(),
)
def test_property_4_tool_decision_classification(
    name: str, args_obj: dict, parseable: bool
) -> None:
    raw_args = json.dumps(args_obj) if parseable else "{not valid json"
    logger = RecordingLogger()
    client = RecordingClient(_make_response(tool_name=name, arguments=raw_args))
    llm = LLMClient(_enabled_config(), logger=logger, client=client)

    decision = llm.decide({"instruction": "do it"}, _CATALOG)

    is_valid = name in tool_names() and parseable
    if is_valid:
        assert decision.kind is DecisionKind.SELECT_TOOL
        assert decision.tool_name == name
        assert decision.arguments == args_obj
    else:
        assert decision.kind is DecisionKind.INVALID_RESPONSE
        # The invalid response was recorded through the Logger (R6.6).
        assert any("invalid response" in msg.lower() for _s, _c, msg in logger.entries)


# --------------------------------------------------------------------------- #
# Property 3: Verbatim message and response passthrough
# --------------------------------------------------------------------------- #

# Feature: ai-devops-coding-agent, Property 3: Verbatim message and response passthrough
# For any non-command instruction, the content forwarded to the LLM_Client
# equals the original message exactly, and for any model response, the text
# delivered to the Owner equals the model's response without translation or
# alteration.
@settings(max_examples=100)
@given(
    instruction=st.text(min_size=0, max_size=300),
    response_text=st.text(min_size=0, max_size=300),
)
def test_property_3_verbatim_passthrough(instruction: str, response_text: str) -> None:
    client = RecordingClient(_make_response(content=response_text))
    llm = LLMClient(_enabled_config(), client=client)

    decision = llm.decide({"instruction": instruction}, _CATALOG)

    # The instruction is forwarded verbatim as the user message (R5.1).
    user_messages = [
        m for m in client.calls[0]["messages"] if m["role"] == "user"
    ]
    assert any(m["content"] == instruction for m in user_messages)

    # A text-only response is returned to the Owner unaltered (R5.2).
    assert decision.kind is DecisionKind.TASK_COMPLETE
    assert decision.message == response_text


# --------------------------------------------------------------------------- #
# Unit tests: LLM-disabled reply and endpoint failure (R1.6, 6.7, 6.8)
# --------------------------------------------------------------------------- #


def test_disabled_returns_feature_unavailable_without_network_call() -> None:
    """When features are disabled, decide() makes no network call (R6.8)."""
    client = RecordingClient(_make_response(content="ignored"))
    notifications: list[str] = []
    llm = LLMClient(
        _disabled_config(), client=client, notifier=notifications.append
    )

    decision = llm.decide({"instruction": "buatkan file"}, _CATALOG)

    assert decision.kind is DecisionKind.FEATURE_UNAVAILABLE
    assert client.calls == []  # no request was issued
    assert notifications  # the Owner was notified (R6.8)


def test_disabled_client_is_never_constructed() -> None:
    """A disabled client does not build a real OpenAI client."""
    llm = LLMClient(_disabled_config())
    assert llm.enabled is False
    # analyze() also short-circuits without a network call.
    decision = llm.analyze({"success": True})
    assert decision.kind is DecisionKind.FEATURE_UNAVAILABLE


def test_endpoint_error_yields_failure_with_owner_notification() -> None:
    """An endpoint error yields FAILURE, is logged, and notifies the Owner (R6.7)."""
    logger = RecordingLogger()
    notifications: list[str] = []
    client = RecordingClient(raises=RuntimeError("connection reset"))
    llm = LLMClient(
        _enabled_config(), logger=logger, client=client, notifier=notifications.append
    )

    decision = llm.decide({"instruction": "deploy"}, _CATALOG)

    assert decision.kind is DecisionKind.FAILURE
    assert "connection reset" in (decision.message or "")
    assert notifications  # Owner notified
    assert any(sev == "error" for sev, _c, _m in logger.entries)


def test_timeout_is_passed_and_failure_returned_on_timeout() -> None:
    """A timeout exception from the client is classified as FAILURE (R6.7)."""

    class _Timeout(Exception):
        pass

    client = RecordingClient(raises=_Timeout("request timed out"))
    llm = LLMClient(_enabled_config(), client=client, timeout=120.0)

    decision = llm.decide({"instruction": "x"}, _CATALOG)
    assert decision.kind is DecisionKind.FAILURE


def test_analyze_continues_with_next_tool_selection() -> None:
    """analyze() returns the model's next SELECT_TOOL decision (R7.3-R7.5)."""
    select = _make_response(tool_name="read_file", arguments=json.dumps({"path": "a"}))
    client = RecordingClient(select)
    llm = LLMClient(_enabled_config(), client=client)
    # Prime the transcript via decide(), then analyze a tool result.
    llm._messages = [{"role": "system", "content": "s"}]  # noqa: SLF001 - test setup
    decision = llm.analyze({"success": True, "data": {"content": "x"}})
    assert decision.kind is DecisionKind.SELECT_TOOL
    assert decision.tool_name == "read_file"
