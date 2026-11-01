"""State of the turn being answered, shared by the client, the routing middleware and the tools."""

import contextvars
from dataclasses import dataclass, field

from aiglasses.assistant.tools.base import ToolContext


@dataclass
class TurnReport:
    lane: int = 0  # 0 rules, 1 local model, 2 cloud
    action: str = ""
    category: str = ""  # what answered, or why the cloud was used
    steps: int = 0
    local_first_token_s: float | None = None
    local_total_s: float | None = None
    cloud_first_token_s: float | None = None
    tools: list[tuple[str, str]] = field(default_factory=list)  # (tool, outcome category)
    skip_memory: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Turn:
    ctx: ToolContext
    report: TurnReport
    enabled: tuple[str, ...]
    use_cloud: bool = False
    reason: str = ""
    plan: list[str] = field(default_factory=list)  # tools the next model steps must call
    leaked: bool = False  # the local model wrote a tool call as plain text

    def escalate(self, reason: str) -> None:
        self.use_cloud, self.reason = True, self.reason or reason
        self.report.category = self.report.category or reason


current_turn: contextvars.ContextVar[Turn] = contextvars.ContextVar("current_turn")
