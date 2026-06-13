"""Property-based tests for the Server_Registry (Properties 17, 18, 19)."""

from __future__ import annotations

from typing import Optional

from cryptography.fernet import Fernet
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.database.connection import open_database
from agent.memory.server_registry import (
    NAME_MAX_LEN,
    PORT_MAX,
    PORT_MIN,
    USERNAME_MAX_LEN,
    ServerRegistry,
)
from agent.security import SecurityManager

_FERNET_KEY = Fernet.generate_key()

# Text without NUL bytes or surrogates (SQLite text columns reject both).
_safe_text = st.text(
    alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)), max_size=120
)
_opt_secret = st.one_of(
    st.none(),
    st.text(alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)), max_size=40),
)


def _make_registry() -> ServerRegistry:
    db = open_database(":memory:")
    security = SecurityManager(fernet_key=_FERNET_KEY)
    return ServerRegistry(db, security)


def _is_valid(
    name: str,
    host: str,
    port: int,
    username: str,
    ssh_key: Optional[str],
    password: Optional[str],
) -> bool:
    """Reference predicate mirroring Requirement 11.1/11.7 constraints."""
    name_ok = 1 <= len(name) <= NAME_MAX_LEN
    host_ok = len(host) >= 1
    port_ok = PORT_MIN <= port <= PORT_MAX
    username_ok = 1 <= len(username) <= USERNAME_MAX_LEN
    credential_ok = (ssh_key is not None and len(ssh_key) >= 1) or (
        password is not None and len(password) >= 1
    )
    return name_ok and host_ok and port_ok and username_ok and credential_ok


# Feature: ai-devops-coding-agent, Property 17: Server registration validation
# For any server registration input, the registration is accepted if and only
# if the name is 1-100 characters, the host is non-empty, the port is an
# integer in 1-65535, the username is 1-100 characters, and at least one of an
# SSH key or a password is present; any input failing a constraint is rejected,
# is not persisted, and yields an error identifying the invalid or missing
# value.
@settings(max_examples=100)
@given(
    name=_safe_text,
    host=_safe_text,
    port=st.integers(min_value=-10, max_value=70000),
    username=_safe_text,
    ssh_key=_opt_secret,
    password=_opt_secret,
)
def test_property_17_server_registration_validation(
    name, host, port, username, ssh_key, password
) -> None:
    registry = _make_registry()
    result = registry.register(
        name, host, port, username, ssh_key=ssh_key, password=password
    )
    expected = _is_valid(name, host, port, username, ssh_key, password)
    assert result.success is expected
    if expected:
        assert registry.count() == 1
    else:
        # Rejected registrations are not persisted and carry an identifying error.
        assert registry.count() == 0
        assert result.error is not None and result.error != ""


# Feature: ai-devops-coding-agent, Property 18: Duplicate server name rejection
# For any registry state, registering a server whose name equals an existing
# Registered_Server's name is rejected, the existing server is not overwritten,
# and an error stating the name already exists is returned.
@settings(max_examples=100)
@given(
    name=st.text(
        alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)),
        min_size=1,
        max_size=100,
    ),
    host_a=st.text(
        alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)),
        min_size=1,
        max_size=30,
    ),
    host_b=st.text(
        alphabet=st.characters(min_codepoint=1, blacklist_categories=("Cs",)),
        min_size=1,
        max_size=30,
    ),
)
def test_property_18_duplicate_server_name_rejection(name, host_a, host_b) -> None:
    registry = _make_registry()

    first = registry.register(name, host_a, 22, "root", password="secret-a")
    assert first.success is True

    second = registry.register(name, host_b, 2222, "admin", password="secret-b")
    assert second.success is False
    assert second.error is not None
    assert "already exists" in second.error

    # The existing server is not overwritten: still one server with host_a.
    servers = registry.list_servers()
    assert len(servers) == 1
    assert servers[0]["name"] == name
    assert servers[0]["host"] == host_a


# Feature: ai-devops-coding-agent, Property 19: Secret exclusion in listings
# For any registry state, listing servers returns each server's name, host,
# port, and username and never includes the stored SSH private key or password.
@settings(max_examples=100)
@given(
    servers=st.lists(
        st.tuples(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=15),
            st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=8, max_size=30),
        ),
        max_size=8,
        unique_by=lambda t: t[0],
    )
)
def test_property_19_secret_exclusion_in_listings(servers) -> None:
    registry = _make_registry()
    secrets: list[str] = []
    for i, (name, secret_body) in enumerate(servers):
        # A marker prefix using characters absent from the public fields
        # guarantees the secret can never be a coincidental substring of them.
        secret = "SECRET!#" + secret_body
        secrets.append(secret)
        assert registry.register(
            name, f"10.0.0.{i}", 22, "root", password=secret
        ).success is True

    listing = registry.list_servers()
    assert len(listing) == len(servers)

    for entry in listing:
        # Only the four public fields are present.
        assert set(entry.keys()) == {"name", "host", "port", "username"}

    # No stored secret value appears anywhere in the rendered listing.
    rendered = repr(listing)
    for secret in secrets:
        assert secret not in rendered
