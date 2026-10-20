"""A remote A2A agent as a LangChain tool.

    uv run python examples/langchain_tool.py                 # offline, against a local agent
    A2A_CARD_URL=https://trips.example.com A2A_TOKEN=... \
        uv run python examples/langchain_tool.py             # a real remote

Needs the ``langchain`` extra (and ``server`` for the offline run). The tool is called
directly here, the way a model would call it; give it to your agent with
``create_agent(model, tools=[tool])`` (from the ``langchain`` package).

The output is the remote's words inside ``<remote-agent-output untrusted="true" ...>``.
When the remote asks for confirmation, the tool returns the question and a note that only
the user may answer; the next call on the SAME tool object is that answer.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from astromesh_a2a import Auth, Policy, RemoteAgent


async def use_tool(agent: RemoteAgent) -> None:
    async with agent:
        await agent.card()  # the tool's name and description come from the card
        tool = agent.as_tool("langchain")
        print(f"tool: {tool.name}\n")
        print(await tool.ainvoke({"query": "world"}), end="\n\n")
        print(await tool.ainvoke({"query": "please delete my notes"}), end="\n\n")
        print(await tool.ainvoke({"query": "no"}))  # the user's answer, relayed by your agent


def remote_agent(url: str) -> RemoteAgent:
    token = os.environ.get("A2A_TOKEN")
    return RemoteAgent(
        url,
        policy=Policy(allowed=(urlsplit(url).hostname or "",)),
        auth=Auth.bearer("A2A_TOKEN") if token else None,
    )


def main() -> None:
    url = os.environ.get("A2A_CARD_URL")
    if url:
        asyncio.run(use_tool(remote_agent(url)))
        return
    # Offline: the local agent from hello_local.py (same loopback opt-in, same scope).
    import secrets
    import socket

    from hello_local import KEY_ENV, local_agent, start_server

    os.environ.setdefault(KEY_ENV, secrets.token_urlsafe(16))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server, thread = start_server(port)
    try:
        asyncio.run(use_tool(local_agent(port)))
    finally:
        server.should_exit = True
        thread.join(5)


if __name__ == "__main__":
    main()
