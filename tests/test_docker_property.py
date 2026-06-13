"""Property tests for the Docker_Manager (Properties 29 and 30).

Both use a mocked docker command runner so no real Docker daemon is required.
"""

from __future__ import annotations

import json
from typing import Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.tools.docker import (
    VALID_RUN_STATES,
    DockerCommandResult,
    DockerManager,
)

# Run states docker reports via ``docker ps --format '{{json .}}'``.
_PS_STATES = sorted(VALID_RUN_STATES - {"removing"})

_id = st.text(alphabet="0123456789abcdef", min_size=12, max_size=64)
_name = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=20
)
_container = st.fixed_dictionaries(
    {"id": _id, "name": _name, "state": st.sampled_from(_PS_STATES)}
)


def _ps_runner(containers: list[dict[str, str]]):
    """A runner that answers ``docker ps`` with the given container records."""

    def runner(args: list[str], timeout: Optional[float]) -> DockerCommandResult:
        if args[:1] == ["ps"]:
            lines = "\n".join(
                json.dumps({"ID": c["id"], "Names": c["name"], "State": c["state"]})
                for c in containers
            )
            return DockerCommandResult(0, lines + ("\n" if lines else ""), "")
        return DockerCommandResult(0, "", "")

    return runner


# Feature: ai-devops-coding-agent, Property 29: Docker container-list completeness
# For any set of containers present on the host, list_containers returns every
# container with its identifier, name, and a run state drawn from the valid set,
# and returns an empty list when no containers are present.
@settings(max_examples=100)
@given(containers=st.lists(_container, max_size=12))
def test_container_list_completeness(containers: list[dict[str, str]]) -> None:
    manager = DockerManager(runner=_ps_runner(containers))
    result = manager.list_containers()

    assert result.success is True
    listed = result.data["containers"]
    assert len(listed) == len(containers)
    for produced, expected in zip(listed, containers):
        assert produced["id"] == expected["id"]
        assert produced["name"] == expected["name"]
        assert produced["state"] == expected["state"]
        assert produced["state"] in VALID_RUN_STATES
    if not containers:
        assert listed == []


# Feature: ai-devops-coding-agent, Property 30: Docker missing-reference safety
# For any start, stop, restart, or deployment that references a container or
# image not present on the host, the Docker_Manager returns a not-found error
# and makes no change to any container or image.
@settings(max_examples=100)
@given(
    reference=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=24
    ),
    operation=st.sampled_from(["start", "stop", "restart", "deploy"]),
)
def test_missing_reference_safety(reference: str, operation: str) -> None:
    mutating: list[str] = []
    _MUTATING = {"start", "stop", "restart", "run", "rm", "kill"}

    def runner(args: list[str], timeout: Optional[float]) -> DockerCommandResult:
        verb = args[0]
        if verb == "inspect" or (verb == "image" and args[1:2] == ["inspect"]):
            # Every reference is absent -> inspect fails.
            return DockerCommandResult(1, "", "Error: No such object")
        if verb in _MUTATING:
            mutating.append(verb)
            return DockerCommandResult(0, "deadbeef", "")
        return DockerCommandResult(0, "", "")

    manager = DockerManager(runner=runner)
    if operation == "start":
        result = manager.start(reference)
    elif operation == "stop":
        result = manager.stop(reference)
    elif operation == "restart":
        result = manager.restart(reference)
    else:
        result = manager.deploy_container(reference)

    assert result.success is False
    assert "not found" in result.error.lower()
    # No mutating docker command may have been issued for a missing reference.
    assert mutating == []
