"""A remote A2A agent as a CrewAI ``BaseTool``.

``run()`` (what crews call) goes through the adapters' background loop (see
``_common``); ``arun()`` awaits directly. Remote failures raise ``RemoteToolError`` with
a safe message, which CrewAI reports to the agent as a tool error.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, require


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    tools = require("crewai.tools", "crewai")
    pydantic = require("pydantic", "crewai")
    remote = RemoteTool.for_agent(agent, name, description)

    class RemoteAgentArgs(pydantic.BaseModel):
        query: str = pydantic.Field(..., description=QUERY_DOC)

    class RemoteAgentTool(tools.BaseTool):
        name: str = remote.name
        description: str = remote.description
        args_schema: type[pydantic.BaseModel] = RemoteAgentArgs

        def _run(self, query: str) -> str:
            return remote.call(query)

        async def _arun(self, query: str) -> str:
            return await remote.acall(query)

    return RemoteAgentTool()
