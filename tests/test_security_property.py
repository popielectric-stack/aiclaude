"""Property-based tests for the Security_Manager (Properties 2, 20, 21, 22)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.logger import Logger
from agent.security import (
    ConfirmationOutcome,
    ConfirmationResponse,
    SecurityManager,
)

_FERNET_KEY = Fernet.generate_key()


def _make_manager(
    *,
    owner_id: Optional[int] = None,
    logger: Optional[Logger] = None,
    server_names: Optional[set[str]] = None,
) -> SecurityManager:
    lookup = None
    if server_names is not None:
        registered = set(server_names)
        lookup = lambda name: name in registered  # noqa: E731 - tiny predicate
    return SecurityManager(
        owner_id=owner_id,
        fernet_key=_FERNET_KEY,
        logger=logger,
        server_name_lookup=lookup,
    )


# Feature: ai-devops-coding-agent, Property 2: Owner-only authorization
# For any incoming message with a sender identifier and any configured Owner
# identifier, the message is forwarded to the Agent_Loop if and only if the
# Owner identifier is non-empty and the sender identifier equals it; otherwise
# the message is discarded, an access-denied notice is returned, and a log
# entry containing the rejected sender identifier and a timestamp is recorded.
@settings(max_examples=100)
@given(
    owner_id=st.one_of(st.none(), st.integers(min_value=1, max_value=10**12)),
    sender_id=st.integers(min_value=1, max_value=10**12),
)
def test_property_2_owner_only_authorization(
    owner_id: Optional[int], sender_id: int
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        logger = Logger(log_dir=tmp, filename="access.log")
        manager = _make_manager(owner_id=owner_id, logger=logger)

        forwarded = manager.authorize(sender_id)
        expected = owner_id is not None and sender_id == owner_id
        assert forwarded is expected

        log_path = Path(tmp) / "access.log"
        if expected:
            # An authorized message is forwarded with no rejection logged.
            assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""
        else:
            # The rejection notice is available and the log records the
            # rejected sender id together with a timestamp.
            assert "Access denied" in manager.access_denied_message()
            contents = log_path.read_text(encoding="utf-8")
            assert str(sender_id) in contents
            # ISO-8601 timestamps rendered by the Logger contain a 'T' separator.
            last_line = contents.strip().splitlines()[-1]
            assert "T" in last_line and "access" in last_line


# Feature: ai-devops-coding-agent, Property 20: Credential encryption round trip
# For any credential value, decrypting the value produced by encrypting it
# returns the original value, and the persisted ciphertext is never equal to
# the plaintext value (no plaintext credential is ever written to the
# Memory_Store).
@settings(max_examples=100)
@given(value=st.text(min_size=0, max_size=200))
def test_property_20_credential_encryption_round_trip(value: str) -> None:
    manager = _make_manager()
    token = manager.encrypt(value)
    assert isinstance(token, bytes)
    # Ciphertext is never the plaintext (Requirements 21.1, 21.2).
    assert token != value.encode("utf-8")
    assert manager.decrypt(token) == value


_YES_WORDS = ["yes", "ya", "iya", "ok", "confirm", "setuju", "lanjut", "boleh"]
_NO_WORDS = ["no", "tidak", "cancel", "batal", "jangan", "stop", "nope"]
_AMBIGUOUS_WORDS = ["maybe", "mungkin", "hmm", "apa", "", "yes no", "ya tidak"]

_REPLY_POOL = (
    [(ConfirmationResponse.YES, w) for w in _YES_WORDS]
    + [(ConfirmationResponse.NO, w) for w in _NO_WORDS]
    + [(ConfirmationResponse.AMBIGUOUS, w) for w in _AMBIGUOUS_WORDS]
)


def _expected_outcome(categories: list[ConfirmationResponse]) -> ConfirmationOutcome:
    """Reference model: first decisive reply within the attempt budget wins."""
    for response in categories[:3]:  # initial reply + 2 re-prompts
        if response is ConfirmationResponse.YES:
            return ConfirmationOutcome.CONFIRMED
        if response is ConfirmationResponse.NO:
            return ConfirmationOutcome.DECLINED
    return ConfirmationOutcome.CANCELLED


# Feature: ai-devops-coding-agent, Property 21: Destructive-action confirmation gating
# For any destructive action and any sequence of Owner replies, the action is
# executed if and only if a reply is interpreted as an affirmative
# confirmation; a reply interpreted as a decline cancels and reports the
# cancellation; an ambiguous or contradictory reply triggers a re-prompt up to
# a maximum of 2 additional attempts, after which the action is cancelled and
# the cancellation is reported.
@settings(max_examples=100)
@given(replies=st.lists(st.sampled_from(_REPLY_POOL), min_size=0, max_size=8))
def test_property_21_destructive_action_confirmation_gating(replies) -> None:
    manager = _make_manager()
    categories = [category for category, _text in replies]
    texts = [text for _category, text in replies]

    # Each pool word is interpreted into its declared category.
    for category, text in replies:
        assert manager.interpret_confirmation(text) is category

    outcome = manager.resolve_confirmation(texts)
    assert outcome is _expected_outcome(categories)
    # Executed if and only if the resolved outcome is CONFIRMED.
    executed = outcome is ConfirmationOutcome.CONFIRMED
    assert executed is (_expected_outcome(categories) is ConfirmationOutcome.CONFIRMED)


# Feature: ai-devops-coding-agent, Property 22: Unauthorized-server blocking
# For any operation that targets a server name absent from the Server_Registry,
# the Security_Manager blocks the operation, returns a not-authorized/not-
# registered error, and no server state is modified.
# Server names are printable identifiers; control characters are out of scope.
_server_name = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.",
    min_size=1,
    max_size=20,
)


@settings(max_examples=100)
@given(
    registered=st.sets(_server_name, max_size=8),
    target=_server_name,
)
def test_property_22_unauthorized_server_blocking(registered: set[str], target: str) -> None:
    manager = _make_manager(server_names=registered)
    result = manager.authorize_server_target(target)
    assert result.success is (target in registered)
    if not result.success:
        assert result.error is not None
        assert "not authorized" in result.error or "not registered" in result.error
        assert target in result.error
