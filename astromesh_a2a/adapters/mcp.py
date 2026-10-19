"""A remote A2A agent as an MCP tool (FastMCP ``Tool``), or a whole MCP server.

``as_tool`` returns a ``mcp.server.fastmcp.tools.Tool``: add it to your server with
``FastMCP(..., tools=[tool])``. ``build_server`` returns a ``FastMCP`` exposing only
that tool (run it with ``server.run()``, stdio by default). Remote failures raise
``ToolError`` with a safe message: the MCP client gets an ``isError`` result.

An MCP server usually serves one client session, but a FastMCP server may serve several:
the tool keeps ONE conversation with the remote, so build one server per user.
"""

from __future__ import annotations

from typing import Any

from astromesh_a2a.adapters._common import QUERY_DOC, RemoteTool, RemoteToolError, require


def _ask(remote: RemoteTool, errors: Any) -> Any:
    async def ask(query: str) -> str:
        try:
            return await remote.acall(query)
        except RemoteToolError as exc:
            raise errors.ToolError(str(exc)) from None

    ask.__doc__ = f"Args:\n    query: {QUERY_DOC}"
    return ask


def as_tool(agent: Any, *, name: str | None = None, description: str | None = None) -> Any:
    tools = require("mcp.server.fastmcp.tools", "mcp")
    errors = require("mcp.server.fastmcp.exceptions", "mcp")
    remote = RemoteTool.for_agent(agent, name, description)
    return tools.Tool.from_function(
        _ask(remote, errors), name=remote.name, description=remote.description
    )


def build_server(
    agent: Any,
    *,
    name: str | None = None,
    description: str | None = None,
    server_name: str = "astromesh-a2a",
) -> Any:
    """A ``FastMCP`` server whose only tool is ``agent`` (see ``as_tool``).

    Run it over **stdio** (``server.run()``, the default): one local client, one
    conversation. The HTTP transports (SSE / streamable HTTP) add NO authentication: if
    you expose one, put it behind your own auth. Every MCP client of this server shares
    the tool's single remote conversation (and its pending task) and calls the remote
    with THIS process's credentials (``agent.auth``), so never serve it to several users.
    """
    fastmcp = require("mcp.server.fastmcp", "mcp")
    return fastmcp.FastMCP(server_name, tools=[as_tool(agent, name=name, description=description)])
