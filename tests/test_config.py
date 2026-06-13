"""Unit tests for Config_Loader edge cases.

Covers missing-token startup termination (R1.2), Pydantic type validation
(R1.5), and the unsupported-Python-version halt (R22.9).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent import config
from agent.config import (
    Config,
    MissingRequiredConfigError,
    UnsupportedPythonVersionError,
)


class _RecordingLogger:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def info(self, message: str, *, category: str = "system") -> None:
        self.info_messages.append(message)

    def error(self, message: str, *, category: str = "system") -> None:
        self.error_messages.append(message)


@pytest.mark.parametrize("token_value", [None, "", "   ", "\t\n"])
def test_missing_token_aborts_startup_and_logs(token_value) -> None:
    """Absent/empty/whitespace token aborts startup and names the variable."""
    env: dict[str, str] = {"API_KEY": "k", "BASE_URL": "u", "MODEL": "m"}
    if token_value is not None:
        env["TELEGRAM_BOT_TOKEN"] = token_value
    logger = _RecordingLogger()

    with pytest.raises(MissingRequiredConfigError):
        config.load(environ=env, logger=logger)

    assert len(logger.error_messages) == 1
    assert "TELEGRAM_BOT_TOKEN" in logger.error_messages[0]


def test_successful_load_enables_llm_and_exposes_values() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "OWNER_ID": "12345",
        "API_KEY": "key",
        "BASE_URL": "https://endpoint.example/v1",
        "MODEL": "claude-opus-4.8",
    }
    cfg = config.load(environ=env)

    assert cfg.telegram_bot_token == "tok"
    assert cfg.owner_id == 12345
    assert cfg.api_key == "key"
    assert cfg.base_url == "https://endpoint.example/v1"
    assert cfg.model == "claude-opus-4.8"
    assert cfg.llm_enabled is True


def test_whitespace_values_are_trimmed_and_treated_as_provided() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "  tok  ",
        "API_KEY": " key ",
        "BASE_URL": " url ",
        "MODEL": " m ",
    }
    cfg = config.load(environ=env)
    assert cfg.telegram_bot_token == "tok"
    assert cfg.api_key == "key"
    assert cfg.llm_enabled is True


def test_invalid_owner_id_is_treated_as_absent() -> None:
    env = {"TELEGRAM_BOT_TOKEN": "tok", "OWNER_ID": "not-an-int"}
    cfg = config.load(environ=env)
    assert cfg.owner_id is None
    assert cfg.llm_enabled is False


def test_pydantic_rejects_non_integer_owner_id_type() -> None:
    """Direct construction with a wrong-typed owner_id fails validation."""
    with pytest.raises(ValidationError):
        Config(telegram_bot_token="tok", owner_id="abc")  # type: ignore[arg-type]


def test_pydantic_rejects_empty_token() -> None:
    with pytest.raises(ValidationError):
        Config(telegram_bot_token="")


def test_config_is_frozen() -> None:
    cfg = Config(telegram_bot_token="tok")
    with pytest.raises(ValidationError):
        cfg.telegram_bot_token = "other"  # type: ignore[misc]


def test_check_python_version_rejects_old_runtime() -> None:
    with pytest.raises(UnsupportedPythonVersionError):
        config.check_python_version(version_info=(3, 10, 0))


def test_check_python_version_accepts_supported_runtime() -> None:
    # Should not raise for 3.11 or any later supported runtime.
    config.check_python_version(version_info=(3, 11, 0))
    config.check_python_version(version_info=(3, 13, 1))
