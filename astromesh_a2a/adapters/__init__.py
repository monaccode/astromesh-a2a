"""A remote A2A agent as a native tool of your agent framework.

``RemoteAgent.as_tool(framework)`` (or ``as_tool(agent, framework)``) returns the
framework's own tool object; each adapter module imports its framework on first use and
raises ``MissingExtraError`` (an ``ImportError``) naming the extra to install.

=================  =====================================  ===========================
``framework``      returns                                 tool errors
=================  =====================================  ===========================
langchain          ``StructuredTool`` (sync + async)       ``ToolException`` (handled)
crewai             ``BaseTool`` (``run`` + ``arun``)        exception
adk                ``FunctionTool`` (async)                ``{"error": ...}``
openai-agents      ``FunctionTool`` (async)                ``failure_error_function``
pydantic-ai        ``Tool`` (async)                        ``ModelRetry``
llamaindex         ``FunctionTool`` (sync + async)         exception
smolagents         ``Tool`` (sync)                         exception
agent-framework    ``FunctionTool`` (async)                exception
mcp                FastMCP ``Tool`` (async)                ``ToolError``
=================  =====================================  ===========================

The tool takes one ``query: str`` and keeps the conversation (and a pending task) on
the tool object: one tool per conversation. See ``astromesh_a2a.adapters._common``.

Building your own tool around a ``RemoteAgent`` (a framework not listed, a custom
loop)? Reuse the same safety text: ``tool_description(card_name, card_description)`` is
the default description (the remote's self-description quoted as untrusted),
``wrap_untrusted(result)`` the output block, and ``PENDING_NOTE`` the note it appends
when the remote is waiting for input.
``astromesh_a2a.adapters.mcp.build_server`` serves the tool as a whole MCP server.

The other way round, ``astromesh_a2a.adapters.server_langchain.handler_from_runnable``
turns a LangChain runnable into a ``serve()`` handler.
"""

from __future__ import annotations

import importlib
from typing import Any

from astromesh_a2a.adapters._common import (
    PENDING_NOTE,
    MissingExtraError,
    RemoteTool,
    RemoteToolError,
    run_sync,
    tool_description,
    wrap_untrusted,
)

__all__ = [
    "FRAMEWORKS",
    "PENDING_NOTE",
    "MissingExtraError",
    "RemoteTool",
    "RemoteToolError",
    "as_tool",
    "run_sync",
    "tool_description",
    "wrap_untrusted",
]

FRAMEWORKS = {
    "langchain": "langchain",
    "crewai": "crewai",
    "adk": "adk",
    "openai-agents": "openai_agents",
    "pydantic-ai": "pydantic_ai",
    "llamaindex": "llamaindex",
    "smolagents": "smolagents",
    "agent-framework": "agent_framework",
    "mcp": "mcp",
}


def as_tool(
    agent: Any, framework: str, *, name: str | None = None, description: str | None = None
) -> Any:
    """``agent`` as a tool of ``framework`` (a key of ``FRAMEWORKS``; ``_`` works for ``-``).

    ``name``/``description`` default to the agent's cached card (``await agent.card()``
    first); the name is sanitized for every framework, the remote's description is quoted
    as untrusted. No I/O happens here.
    """
    key = framework.strip().lower().replace("_", "-") if isinstance(framework, str) else ""
    if key not in FRAMEWORKS:
        raise ValueError(f"unknown framework {framework!r}; expected one of {sorted(FRAMEWORKS)}")
    module = importlib.import_module(f"astromesh_a2a.adapters.{FRAMEWORKS[key]}")
    return module.as_tool(agent, name=name, description=description)
