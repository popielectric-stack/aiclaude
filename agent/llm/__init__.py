"""Language-model integration for the AI DevOps & Coding Agent.

Communicates with the Claude Opus 4.8 model through an OpenAI-compatible API
endpoint and drives tool selection for the agent loop.
"""

from agent.llm.catalog import (
    CATALOG,
    TOOL_SPECS,
    ToolSpec,
    build_catalog,
    get_spec,
    tool_names,
)
from agent.llm.client import SYSTEM_PROMPT, LLMClient

__all__ = [
    "CATALOG",
    "TOOL_SPECS",
    "ToolSpec",
    "build_catalog",
    "get_spec",
    "tool_names",
    "LLMClient",
    "SYSTEM_PROMPT",
]
