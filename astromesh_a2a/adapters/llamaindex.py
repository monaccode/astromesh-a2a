"""A remote A2A agent as a LlamaIndex ``FunctionTool``.

``call``/``acall`` both work; the sync path runs on the adapters' background loop (see
``_common``). Remote failures raise ``RemoteToolError`` with a safe message; LlamaIndex
agents turn a raised tool exception into an error tool output for the model.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    li = require("llama_index.core.tools", "llamaindex")
    remote = RemoteTool.for_agent(agent, name, description)

    async def ask(query: str) -> str:
        return await remote.acall(query)

    def ask_sync(query: str) -> str:
        return remote.call(query)

    ask.__doc__ = ask_sync.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return li.FunctionTool.from_defaults(
        fn=ask_sync, async_fn=ask, name=remote.name, description=remote.description
    )
