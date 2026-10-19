"""A remote A2A agent as a PydanticAI ``Tool`` (async; needs Python >= 3.11).

Remote failures raise ``ModelRetry`` with the safe message, PydanticAI's convention for
a tool error the model should see (it counts against the tool's ``max_retries``).
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, RemoteToolError, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    pai = require("pydantic_ai", "pydantic-ai")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> str:
        try:
            return await remote.acall(query)
        except RemoteToolError as exc:
            raise pai.ModelRetry(str(exc)) from None

    ask.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return pai.Tool(ask, takes_ctx=False, name=remote.name, description=remote.description)
