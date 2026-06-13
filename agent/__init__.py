"""AI DevOps & Coding Agent package.

A single, long-lived Python 3.11+ process controlled exclusively by one
Telegram user (the Owner). It forwards natural-language instructions to a
Claude Opus 4.8 model exposed through an OpenAI-compatible endpoint and
executes the resulting plan via a set of internal tools.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
