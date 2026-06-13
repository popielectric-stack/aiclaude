"""Configuration loading and validation (``Config_Loader``).

Reads the four Configuration_Variables -- ``TELEGRAM_BOT_TOKEN``, ``API_KEY``,
``BASE_URL``, ``MODEL`` -- plus the Owner identifier from the process
environment only. Absent, empty, or whitespace-only values are treated as
**not provided** (Requirement 1.1).

Startup rules:

* Missing ``TELEGRAM_BOT_TOKEN`` -> log the named missing variable and abort
  startup *before* the Telegram interface connects (Requirement 1.2). This is
  surfaced as :class:`MissingRequiredConfigError`.
* Missing any of ``API_KEY`` / ``BASE_URL`` / ``MODEL`` -> complete startup
  with ``llm_enabled = False`` and log each missing variable by name
  (Requirement 1.3).
* On success, a validated Pydantic :class:`Config` is returned for the
  Telegram interface and the LLM client (Requirements 1.4, 1.5).

A runtime guard, :func:`check_python_version`, halts on Python earlier than
3.11 (Requirement 22.9).
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field

# The three Configuration_Variables that together gate the language-model
# features. ``TELEGRAM_BOT_TOKEN`` is handled separately because its absence
# aborts startup entirely.
LLM_CONFIG_VARS: tuple[str, ...] = ("API_KEY", "BASE_URL", "MODEL")

# Minimum supported Python version (Requirements 22.4, 22.9).
MINIMUM_PYTHON_VERSION: tuple[int, int] = (3, 11)


class ConfigError(Exception):
    """Base class for configuration errors raised at startup."""


class MissingRequiredConfigError(ConfigError):
    """Raised when ``TELEGRAM_BOT_TOKEN`` is not provided (Requirement 1.2)."""


class UnsupportedPythonVersionError(ConfigError):
    """Raised when the runtime is older than Python 3.11 (Requirement 22.9)."""


class _SupportsLogging(Protocol):
    """Minimal logger contract the Config_Loader depends on.

    The real :class:`agent.logger.Logger` satisfies this protocol; tests may
    pass a lightweight recording double.
    """

    def info(self, message: str, *, category: str = ...) -> object: ...

    def error(self, message: str, *, category: str = ...) -> object: ...


def is_provided(value: Optional[str]) -> bool:
    """Return ``True`` when ``value`` counts as provided (Requirement 1.1).

    A value is *not provided* when it is ``None``, the empty string, or
    consists solely of whitespace characters.
    """
    return value is not None and value.strip() != ""


def detect_missing_llm_vars(environ: Mapping[str, Optional[str]]) -> list[str]:
    """Return the names of the LLM Configuration_Variables not provided.

    The returned list preserves the canonical ordering in
    :data:`LLM_CONFIG_VARS` so the startup log names variables deterministically
    (Requirement 1.3, Property 1).
    """
    return [name for name in LLM_CONFIG_VARS if not is_provided(environ.get(name))]


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalize a raw env value to a provided string or ``None``."""
    return value.strip() if is_provided(value) else None


def _parse_owner_id(value: Optional[str]) -> Optional[int]:
    """Parse the Owner identifier from its raw env value.

    Returns ``None`` when the value is not provided or is not a valid integer;
    access control treats an absent Owner id as "deny all" at message time
    (Requirement 3.5), so an unparseable value must not abort startup.
    """
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


class Config(BaseModel):
    """Validated, immutable configuration object (Requirement 1.5).

    ``llm_enabled`` is a derived property: it is ``True`` if and only if all of
    ``api_key``, ``base_url``, and ``model`` are provided (Requirement 1.3,
    Property 1).
    """

    model_config = ConfigDict(frozen=True)

    telegram_bot_token: str = Field(min_length=1)
    owner_id: Optional[int] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_enabled(self) -> bool:
        """True iff ``api_key``, ``base_url`` and ``model`` are all provided."""
        return all(
            value is not None and value.strip() != ""
            for value in (self.api_key, self.base_url, self.model)
        )


def load(
    environ: Optional[Mapping[str, str]] = None,
    logger: Optional[_SupportsLogging] = None,
) -> Config:
    """Load and validate configuration from the environment.

    Args:
        environ: Mapping to read from; defaults to ``os.environ``. Reading is
            restricted to this mapping (Requirement 1.1).
        logger: Optional logger used to record missing-variable diagnostics. A
            log record is emitted for the missing ``TELEGRAM_BOT_TOKEN``
            (Requirement 1.2) and for each missing LLM variable (Requirement
            1.3).

    Returns:
        A validated :class:`Config`.

    Raises:
        MissingRequiredConfigError: when ``TELEGRAM_BOT_TOKEN`` is not provided.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ

    token = _clean(env.get("TELEGRAM_BOT_TOKEN"))
    if token is None:
        message = (
            "Missing required configuration variable: TELEGRAM_BOT_TOKEN. "
            "Agent startup aborted before connecting to the Telegram Bot API."
        )
        if logger is not None:
            logger.error(message, category="system")
        raise MissingRequiredConfigError(message)

    missing_llm = detect_missing_llm_vars(env)
    if missing_llm and logger is not None:
        logger.info(
            "Language-model features disabled; missing configuration "
            f"variable(s): {', '.join(missing_llm)}.",
            category="system",
        )

    return Config(
        telegram_bot_token=token,
        owner_id=_parse_owner_id(env.get("OWNER_ID")),
        api_key=_clean(env.get("API_KEY")),
        base_url=_clean(env.get("BASE_URL")),
        model=_clean(env.get("MODEL")),
    )


def check_python_version(
    version_info: tuple[int, ...] | None = None,
) -> None:
    """Halt startup on Python runtimes earlier than 3.11 (Requirement 22.9).

    Args:
        version_info: Version tuple to check; defaults to the running
            interpreter's ``sys.version_info``.

    Raises:
        UnsupportedPythonVersionError: when the runtime is older than 3.11.
    """
    current = tuple(sys.version_info[:2]) if version_info is None else tuple(version_info[:2])
    if current < MINIMUM_PYTHON_VERSION:
        running = ".".join(str(part) for part in current)
        required = ".".join(str(part) for part in MINIMUM_PYTHON_VERSION)
        raise UnsupportedPythonVersionError(
            f"Unsupported Python version {running}: the Agent requires Python "
            f"{required} or later."
        )
