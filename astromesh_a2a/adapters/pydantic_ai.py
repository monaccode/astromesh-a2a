"""A remote A2A agent as a PydanticAI ``Tool`` (async; needs Python >= 3.11).

An ``A2ATimeout`` raises ``ModelRetry`` (worth retrying; it counts against the tool's
``max_retries``). Any other remote failure is not: its safe message is returned as the
tool result, so the model sees it without burning retries.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, RemoteToolError, require
from astromesh_a2a.errors import A2ATimeout


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    pai = require("pydantic_ai", "pydantic-ai")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> str:
        try:
            return await remote.acall(query)
        except RemoteToolError as exc:
            if issubclass(exc.error_type, A2ATimeout):
                raise pai.ModelRetry(str(exc)) from None
            return str(exc)

    ask.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return pai.Tool(ask, takes_ctx=False, name=remote.name, description=remote.description)
