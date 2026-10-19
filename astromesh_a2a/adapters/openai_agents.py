"""A remote A2A agent as an OpenAI Agents SDK ``FunctionTool`` (async).

Remote failures go through the tool's ``failure_error_function``, which hands the model
the safe message (anything that is not ours only shows its class name).
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, error_message, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    agents = require("agents", "openai-agents")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> str:
        return await remote.acall(query)

    ask.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return agents.function_tool(
        ask,
        name_override=remote.name,
        description_override=remote.description,
        failure_error_function=lambda _ctx, exc: error_message(exc),
    )
