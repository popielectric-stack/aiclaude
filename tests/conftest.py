"""Shared test configuration for the AI DevOps & Coding Agent test suite.

Registers a Hypothesis settings profile that disables the per-example deadline
(some property tests exercise SQLite/filesystem I/O whose timing is noisy in
CI) while keeping the minimum example count required by the design's Testing
Strategy. Individual property tests still declare ``@settings(max_examples=100)``
explicitly, which is authoritative for those tests.
"""

from hypothesis import HealthCheck, settings

settings.register_profile(
    "agent",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile("agent")
