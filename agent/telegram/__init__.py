"""Telegram interface for the AI DevOps & Coding Agent.

Connects to the Telegram Bot API, dispatches slash commands, and renders
results back to the Owner.
"""

from agent.telegram.commands import (
    COMMAND_DESCRIPTIONS,
    CommandDispatcher,
    CommandResult,
)
from agent.telegram.interface import TelegramInterface

__all__ = [
    "CommandDispatcher",
    "CommandResult",
    "COMMAND_DESCRIPTIONS",
    "TelegramInterface",
]
