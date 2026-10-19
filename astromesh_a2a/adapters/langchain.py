"""A remote A2A agent as a LangChain ``StructuredTool``.

Sync (``invoke``) and async (``ainvoke``) both work; the sync path runs on the adapters'
background loop (see ``_common``). Remote failures raise ``ToolException`` and the tool
has ``handle_tool_error=True``: the model sees the safe message, the run goes on.
Works with ``langchain.agents.create_agent`` and LangGraph's ``ToolNode``.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, RemoteToolError, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    lc = require("langchain_core.tools", "langchain")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> str:
        try:
            return await remote.acall(query)
        except RemoteToolError as exc:
            raise lc.ToolException(str(exc)) from None

    def ask_sync(query: str) -> str:
        try:
            return remote.call(query)
        except RemoteToolError as exc:
            raise lc.ToolException(str(exc)) from None

    ask.__doc__ = ask_sync.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return lc.StructuredTool.from_function(
        func=ask_sync,
        coroutine=ask,
        name=remote.name,
        description=remote.description,
        handle_tool_error=True,
    )
