"""Which model answers each step: the local one, unless the turn was handed to the cloud."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from aiglasses.assistant.agent.turn import current_turn

log = logging.getLogger("aiglasses.agent")
Handler = Callable[[Any], Awaitable[Any]]


class ModelRouter(AgentMiddleware):
    """Local first. A local step that fails or is too slow is repeated on the cloud model, and
    once the model asks for `think_harder` the rest of the turn runs there."""

    def __init__(self, local: Any, cloud: Any, step_timeout_s: float) -> None:
        super().__init__()
        self._local, self._cloud, self._timeout = local, cloud, step_timeout_s

    async def awrap_model_call(self, request: Any, handler: Handler) -> Any:
        turn = current_turn.get()
        turn.report.steps += 1
        if turn.plan:  # the words asked for a tool: this step must use the next planned one
            # llama-server only takes a string tool_choice: offer just that tool and require a call
            only = [t for t in request.tools if getattr(t, "name", None) == turn.plan[0]]
            if only:
                request = request.override(tools=only, tool_choice="required")
            turn.plan.pop(0)
        if not turn.use_cloud:
            try:
                response = await asyncio.wait_for(
                    handler(request.override(model=self._local)), self._timeout
                )
            except Exception as exc:  # any local failure: the cloud repeats this step
                log.warning("local model step failed: %s: %s", type(exc).__name__, exc)
                turn.escalate(
                    "local_slow" if isinstance(exc, asyncio.TimeoutError) else "local_down"
                )
            else:
                turn.report.lane = max(turn.report.lane, 1)
                return response
        turn.report.lane = 2
        return await handler(request.override(model=self._cloud))
