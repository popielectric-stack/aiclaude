"""Unit tests for Server_Registry capacity bounds (Requirement 11.5)."""

from __future__ import annotations

from cryptography.fernet import Fernet

from agent.database.connection import open_database
from agent.memory.server_registry import MAX_SERVERS, ServerRegistry
from agent.security import SecurityManager


def _make_registry() -> ServerRegistry:
    db = open_database(":memory:")
    security = SecurityManager(fernet_key=Fernet.generate_key())
    return ServerRegistry(db, security)


def test_two_servers_can_be_stored() -> None:
    """The registry stores two or more servers simultaneously (R11.5)."""
    registry = _make_registry()
    assert registry.register("alpha", "10.0.0.1", 22, "root", password="a").success is True
    assert registry.register("beta", "10.0.0.2", 22, "root", password="b").success is True
    assert registry.count() == 2
    assert {s["name"] for s in registry.list_servers()} == {"alpha", "beta"}


def test_registry_accepts_servers_up_to_capacity_then_rejects() -> None:
    """Up to MAX_SERVERS register successfully; the next is rejected (R11.5)."""
    registry = _make_registry()
    for i in range(MAX_SERVERS):
        result = registry.register(f"srv-{i}", f"10.0.0.{i}", 22, "root", password=f"pw-{i}")
        assert result.success is True, f"registration {i} should succeed"
    assert registry.count() == MAX_SERVERS

    overflow = registry.register("one-too-many", "10.1.1.1", 22, "root", password="pw")
    assert overflow.success is False
    assert overflow.error is not None and "full" in overflow.error
    assert registry.count() == MAX_SERVERS


def test_boundary_field_lengths_are_accepted() -> None:
    """Name/username at the 100-character boundary are accepted."""
    registry = _make_registry()
    name = "n" * 100
    username = "u" * 100
    assert registry.register(name, "host", 1, username, ssh_key="key").success is True
    assert registry.register("port-max", "host", 65535, "root", password="p").success is True
