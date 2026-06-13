"""Property-based test for the Config_Loader provisioning detection."""

from __future__ import annotations

from typing import Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from agent import config
from agent.config import LLM_CONFIG_VARS


class _RecordingLogger:
    """Minimal logger double that records messages by severity."""

    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def info(self, message: str, *, category: str = "system") -> None:
        self.info_messages.append(message)

    def error(self, message: str, *, category: str = "system") -> None:
        self.error_messages.append(message)


# A value is "not provided" when absent (None / key omitted), empty, or
# whitespace-only; otherwise it is provided.
_value_strategy = st.one_of(
    st.none(),
    st.just(""),
    st.text(alphabet=" \t\n\r", min_size=1, max_size=4),  # whitespace-only
    st.text(min_size=1, max_size=12).filter(lambda s: s.strip() != ""),  # provided
)


def _build_environ(
    api_key: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
) -> dict[str, str]:
    """Build an environ mapping, omitting keys whose value is ``None``."""
    env: dict[str, str] = {"TELEGRAM_BOT_TOKEN": "valid-token"}
    for name, value in (("API_KEY", api_key), ("BASE_URL", base_url), ("MODEL", model)):
        if value is not None:
            env[name] = value
    return env


# Feature: ai-devops-coding-agent, Property 1: Configuration provisioning detection
# For any mapping of values to the variables API_KEY, BASE_URL, and MODEL, the
# Config_Loader treats a value that is absent, empty, or whitespace-only as not
# provided, sets llm_enabled to true if and only if all three are provided, and
# the startup log names exactly the variables that are not provided.
@settings(max_examples=100)
@given(api_key=_value_strategy, base_url=_value_strategy, model=_value_strategy)
def test_property_1_configuration_provisioning_detection(
    api_key: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
) -> None:
    env = _build_environ(api_key, base_url, model)
    logger = _RecordingLogger()

    cfg = config.load(environ=env, logger=logger)

    raw_values = {"API_KEY": api_key, "BASE_URL": base_url, "MODEL": model}
    expected_provided = {
        name: (value is not None and value.strip() != "")
        for name, value in raw_values.items()
    }
    expected_missing = {name for name, provided in expected_provided.items() if not provided}

    # llm_enabled is true iff all three are provided.
    assert cfg.llm_enabled == (len(expected_missing) == 0)

    if expected_missing:
        # Exactly one diagnostic message is logged, naming exactly the missing
        # variables and no others.
        assert len(logger.info_messages) == 1
        message = logger.info_messages[0]
        named = {name for name in LLM_CONFIG_VARS if name in message}
        assert named == expected_missing
    else:
        # Nothing is missing, so no provisioning diagnostic is logged.
        assert logger.info_messages == []
