"""The assistant's brain: fast rules first, then a LangChain agent that prefers the local model."""

from aiglasses.assistant.agent.client import AgentClient
from aiglasses.assistant.agent.turn import TurnReport

__all__ = ["AgentClient", "TurnReport"]
