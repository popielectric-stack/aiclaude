"""The Security_Manager: access control, credential crypto, and confirmation.

Responsibilities (Requirements 3, 11.4, 12.8, 21):

* :meth:`SecurityManager.authorize` -- forward a message to the Agent_Loop iff
  the configured Owner identifier is present and equals the sender; otherwise
  the message is denied and a log entry recording the rejected sender id and a
  timestamp is written (Requirements 3.1-3.5).
* :meth:`SecurityManager.encrypt` / :meth:`SecurityManager.decrypt` -- Fernet
  symmetric encryption used to protect credentials at rest. Encryption failure
  raises :class:`EncryptionError` so the caller aborts the store without ever
  writing plaintext (Requirement 21.9); decryption failure raises
  :class:`DecryptionError` so the dependent operation aborts (Requirement
  21.10). The plaintext is never persisted (Requirements 11.4, 21.1, 21.2).
* :meth:`SecurityManager.is_destructive` -- classify a selected action as a
  ``Destructive_Action`` so the Agent_Loop can gate it behind confirmation.
* :meth:`SecurityManager.interpret_confirmation` -- parse an Owner reply into
  ``YES`` / ``NO`` / ``AMBIGUOUS`` (Indonesian or English) (Requirement 21.5).
* :meth:`SecurityManager.resolve_confirmation` -- the pure confirmation-gating
  state machine: execute iff an affirmative reply is given, cancel on a
  decline, re-prompt on an ambiguous reply up to a maximum of 2 additional
  attempts, then cancel (Requirements 21.5, 21.6, 21.7, 21.11).
* :meth:`SecurityManager.authorize_server_target` -- block operations that
  target a server name absent from the Server_Registry (Requirements 12.8,
  21.8).
"""

from __future__ import annotations

import base64
import enum
import hashlib
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from cryptography.fernet import Fernet, InvalidToken

from agent.models import ToolResult


class SecurityError(Exception):
    """Base class for Security_Manager failures."""


class EncryptionError(SecurityError):
    """Raised when a credential value cannot be encrypted (Requirement 21.9)."""


class DecryptionError(SecurityError):
    """Raised when a stored credential cannot be decrypted (Requirement 21.10)."""


class ConfirmationResponse(str, enum.Enum):
    """Interpretation of an Owner reply to a confirmation prompt (R21.5)."""

    YES = "YES"
    NO = "NO"
    AMBIGUOUS = "AMBIGUOUS"


class ConfirmationOutcome(str, enum.Enum):
    """Final outcome of a destructive-action confirmation flow.

    * ``CONFIRMED``  -- an affirmative reply was given; execute the action.
    * ``DECLINED``   -- the Owner declined; cancel and report (R21.6).
    * ``CANCELLED``  -- no clear yes/no after the allowed re-prompts; cancel
      and report (R21.11).
    """

    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


# Maximum number of *additional* re-prompts allowed after the first reply
# before a destructive action is cancelled (Requirements 21.5, 21.11).
MAX_REPROMPTS: int = 2

# Tokens that unambiguously affirm a confirmation (English + Indonesian).
_AFFIRMATIVE_TOKENS: frozenset[str] = frozenset(
    {
        "yes", "y", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm",
        "confirmed", "proceed", "go", "do", "accept", "affirmative",
        "ya", "iya", "iyaa", "yoi", "yups", "betul", "benar", "lanjut",
        "lanjutkan", "setuju", "boleh", "gas", "oke", "sip",
    }
)

# Tokens that unambiguously decline a confirmation (English + Indonesian).
_NEGATIVE_TOKENS: frozenset[str] = frozenset(
    {
        "no", "n", "nope", "nah", "cancel", "stop", "abort", "decline",
        "negative", "dont", "don't",
        "tidak", "tdk", "gak", "ga", "nggak", "ngga", "enggak", "jangan",
        "batal", "batalkan", "tolak",
    }
)

# Tool names that are always destructive regardless of their arguments.
_ALWAYS_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"delete_file"})

# Substrings that, when present in a tool name, mark the action destructive.
_DESTRUCTIVE_NAME_KEYWORDS: tuple[str, ...] = (
    "delete", "remove", "destroy", "drop", "restart", "reboot",
    "shutdown", "kill", "rmdir", "purge", "wipe",
)

# Tool names whose argument payload carries a shell command to inspect.
_COMMAND_TOOLS: frozenset[str] = frozenset(
    {"run_command", "run_remote_command", "execute", "send_console_command"}
)

# Power-signal tool names; a stop/restart/kill signal is destructive.
_POWER_TOOLS: frozenset[str] = frozenset({"power", "power_signal", "set_power"})
_DESTRUCTIVE_POWER_SIGNALS: frozenset[str] = frozenset({"stop", "restart", "kill"})

# Patterns that mark a shell command string as destructive.
_DESTRUCTIVE_COMMAND_PATTERNS: tuple[str, ...] = (
    "rm -rf", "rm -fr", "rm -r", "rm -f", "rm ", "rmdir",
    "drop database", "drop table", "truncate", "mkfs", "dd if=", "dd ",
    "shutdown", "reboot", "halt", "poweroff",
    "git push --force", "git push -f", "git reset --hard", "git clean -fd",
    "docker rm", "docker rmi", "docker volume rm", "docker system prune",
    "systemctl stop", "systemctl restart", "service stop",
    "kill ", "pkill", "killall", "> /dev", "mv -f",
)


class _SupportsLogging(Protocol):
    """Minimal logger contract used to record access rejections."""

    def log(self, severity: Any, category: str, message: str, **kwargs: Any) -> Any: ...


def key_from_secret(secret: str) -> bytes:
    """Derive a deterministic 32-byte Fernet key from an arbitrary secret.

    The secret (supplied via the environment) is hashed with SHA-256 and
    url-safe base64 encoded, producing a valid Fernet key. Deterministic
    derivation lets the Agent decrypt credentials persisted in earlier runs
    using the same secret.
    """
    if not secret:
        raise ValueError("A non-empty secret is required to derive an encryption key.")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecurityManager:
    """Enforces owner-only access, credential crypto, and confirmation gating."""

    def __init__(
        self,
        *,
        owner_id: Optional[int] = None,
        fernet: Optional[Fernet] = None,
        fernet_key: Optional[bytes] = None,
        secret: Optional[str] = None,
        logger: Optional[_SupportsLogging] = None,
        server_name_lookup: Optional[Callable[[str], bool]] = None,
    ) -> None:
        """Create a Security_Manager.

        Exactly one source of the encryption key must be supplied:
        ``fernet`` (a ready instance, useful for tests), ``fernet_key`` (a raw
        url-safe base64 key), or ``secret`` (a passphrase from which a key is
        derived via :func:`key_from_secret`).
        """
        if fernet is not None:
            self._fernet: Fernet = fernet
        elif fernet_key is not None:
            self._fernet = Fernet(fernet_key)
        elif secret is not None:
            self._fernet = Fernet(key_from_secret(secret))
        else:
            raise ValueError(
                "SecurityManager requires one of 'fernet', 'fernet_key', or 'secret'."
            )
        self._owner_id = owner_id
        self._logger = logger
        self._server_name_lookup = server_name_lookup

    # -- Access control (Requirement 3) ----------------------------------- #

    @property
    def owner_id(self) -> Optional[int]:
        """The configured Owner identifier, or ``None`` when not configured."""
        return self._owner_id

    def authorize(self, sender_id: Optional[int]) -> bool:
        """Return ``True`` iff ``sender_id`` is the configured Owner.

        When the Owner identifier is absent/empty the message is denied
        (Requirement 3.5). On any rejection a log entry recording the rejected
        sender id and a timestamp is written (Requirement 3.3).
        """
        if self._owner_id is None:
            self._log_rejection(sender_id, "owner identifier is not configured")
            return False
        if sender_id is None or sender_id != self._owner_id:
            self._log_rejection(sender_id, "sender is not the authorized owner")
            return False
        return True

    @staticmethod
    def access_denied_message() -> str:
        """The notice returned to an unauthorized sender (Requirement 3.2)."""
        return "Access denied: you are not authorized to control this agent."

    def _log_rejection(self, sender_id: Optional[int], reason: str) -> None:
        """Record a rejected message's sender id and timestamp (Requirement 3.3)."""
        if self._logger is None:
            return
        self._logger.log(
            "warning",
            "access",
            f"Access denied for sender id {sender_id!r} ({reason}).",
        )

    # -- Credential crypto (Requirements 11.4, 21.1, 21.2, 21.9, 21.10) ---- #

    def encrypt(self, value: str) -> bytes:
        """Encrypt ``value`` and return the Fernet ciphertext.

        Raises :class:`EncryptionError` on any failure so the caller aborts the
        store without writing plaintext (Requirement 21.9).
        """
        try:
            return self._fernet.encrypt(value.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - any failure must abort the store
            raise EncryptionError(f"Failed to encrypt credential value: {exc}") from exc

    def decrypt(self, token: bytes) -> str:
        """Decrypt a Fernet ``token`` and return the original plaintext value.

        Raises :class:`DecryptionError` on an invalid/corrupted token so the
        dependent operation aborts (Requirement 21.10). The decrypted value is
        returned to the caller for in-memory use only and is never persisted.
        """
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except (InvalidToken, TypeError, ValueError) as exc:
            raise DecryptionError(f"Failed to decrypt stored credential: {exc}") from exc

    # -- Destructive-action classification & confirmation (Requirement 21) - #

    def is_destructive(self, tool_name: str, args: Mapping[str, Any]) -> bool:
        """Classify a selected action as a ``Destructive_Action``.

        An action is destructive when it deletes data, overwrites files, drops
        a database, or stops/restarts a service or server (per the Glossary).
        """
        name = (tool_name or "").strip().lower()
        if not name:
            return False
        if name in _ALWAYS_DESTRUCTIVE_TOOLS:
            return True
        if any(keyword in name for keyword in _DESTRUCTIVE_NAME_KEYWORDS):
            return True
        if name in _POWER_TOOLS:
            signal = str(args.get("signal", "")).strip().lower()
            return signal in _DESTRUCTIVE_POWER_SIGNALS
        if name in _COMMAND_TOOLS:
            command = args.get("command")
            if command is None:
                command = args.get("cmd", "")
            return self._command_is_destructive(str(command))
        return False

    @staticmethod
    def _command_is_destructive(command: str) -> bool:
        """Return ``True`` when a shell command matches a destructive pattern."""
        lowered = command.lower()
        return any(pattern in lowered for pattern in _DESTRUCTIVE_COMMAND_PATTERNS)

    @staticmethod
    def interpret_confirmation(text: Optional[str]) -> ConfirmationResponse:
        """Interpret an Owner reply as YES, NO, or AMBIGUOUS (Requirement 21.5).

        A reply is ``AMBIGUOUS`` when it is empty, contains no recognized
        yes/no token, or is contradictory (contains both an affirmative and a
        negative token).
        """
        if not text:
            return ConfirmationResponse.AMBIGUOUS
        tokens = {
            token.strip(".,!?;:\"'()[]{}")
            for token in text.strip().lower().split()
        }
        tokens.discard("")
        has_yes = bool(tokens & _AFFIRMATIVE_TOKENS)
        has_no = bool(tokens & _NEGATIVE_TOKENS)
        if has_yes and not has_no:
            return ConfirmationResponse.YES
        if has_no and not has_yes:
            return ConfirmationResponse.NO
        return ConfirmationResponse.AMBIGUOUS

    def resolve_confirmation(self, replies: Sequence[str]) -> ConfirmationOutcome:
        """Resolve a destructive-action confirmation from a sequence of replies.

        Considers at most ``1 + MAX_REPROMPTS`` replies (the initial reply plus
        the allowed re-prompts). The first decisive reply determines the
        outcome: an affirmative confirms, a decline cancels. If every considered
        reply is ambiguous, the action is cancelled (Requirements 21.5-21.7,
        21.11).
        """
        budget = 1 + MAX_REPROMPTS
        for reply in list(replies)[:budget]:
            response = self.interpret_confirmation(reply)
            if response is ConfirmationResponse.YES:
                return ConfirmationOutcome.CONFIRMED
            if response is ConfirmationResponse.NO:
                return ConfirmationOutcome.DECLINED
        return ConfirmationOutcome.CANCELLED

    # -- Server-target authorization (Requirements 12.8, 21.8) ------------- #

    def set_server_name_lookup(self, lookup: Callable[[str], bool]) -> None:
        """Inject the predicate used to test whether a server name is registered.

        Wired after construction to break the Security_Manager <-> Server_Registry
        cycle (the registry encrypts secrets through this manager).
        """
        self._server_name_lookup = lookup

    def authorize_server_target(self, server_name: str) -> ToolResult:
        """Authorize an operation that targets ``server_name``.

        Returns a successful :class:`~agent.models.ToolResult` when the server
        is present in the Server_Registry; otherwise blocks the operation and
        returns a not-registered error without modifying any state
        (Requirements 12.8, 21.8).
        """
        if self._server_name_lookup is not None and self._server_name_lookup(server_name):
            return ToolResult.ok({"server": server_name})
        return ToolResult.fail(
            f"Server {server_name!r} is not authorized: no such server is "
            "registered in the Server_Registry."
        )
