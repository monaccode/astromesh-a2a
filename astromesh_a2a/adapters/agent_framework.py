"""A remote A2A agent as a Microsoft Agent Framework ``FunctionTool`` (async).

Remote failures raise ``RemoteToolError`` with a safe message; the framework returns a
raised tool exception to the model as the function result.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    af = require("agent_framework", "agent-framework")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> str:
        return await remote.acall(query)

    ask.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return af.tool(ask, name=remote.name, description=remote.description)
