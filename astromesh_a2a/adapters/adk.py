"""A remote A2A agent as a Google ADK ``FunctionTool`` (async).

ADK takes the tool name from the function's ``__name__`` and the description from its
docstring. A remote failure is returned as ``{"error": "<safe message>"}``, ADK's own
convention for a tool error the model should see.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, RemoteToolError, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    adk = require("google.adk.tools", "adk")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> Any:
        try:
            return await remote.acall(query)
        except RemoteToolError as exc:
            return {"error": str(exc)}

    ask.__name__ = ask.__qualname__ = remote.name
    ask.__doc__ = f"{remote.description}\n\nArgs:\n    query: {QUERY_DOC}"
    return adk.FunctionTool(ask)
