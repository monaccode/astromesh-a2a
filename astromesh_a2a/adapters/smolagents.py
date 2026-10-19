"""A remote A2A agent as a smolagents ``Tool`` (sync).

smolagents calls tools synchronously: calls run on the adapters' background loop (see
``_common``), so a tool call from inside a running event loop is refused. Remote
failures raise ``RemoteToolError`` with a safe message, which smolagents reports to the
model as a tool execution error.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    smol = require("smolagents", "smolagents")
    remote = RemoteTool.for_agent(agent, name, description)

    class RemoteAgentTool(smol.Tool):
        name = remote.name
        description = remote.description
        inputs = {"query": {"type": "string", "description": QUERY_DOC}}  # noqa: RUF012
        output_type = "string"

        def forward(self, query: str) -> str:
            return remote.call(query)

    return RemoteAgentTool()
