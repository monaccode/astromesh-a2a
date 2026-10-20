"""Serve an agent and call it, all on this machine, offline.

    uv run python examples/hello_local.py

Run it from a checkout of the repository (it is not shipped in the wheel), with the
``server`` extra installed (``pip install 'astromesh-a2a[server]'``). It starts
``create_app`` under uvicorn on ``127.0.0.1`` (in a thread), protected by an API key,
then talks to it with ``RemoteAgent``: card, ``send()``, a confirmation that the client
does NOT answer on its own, ``reply()`` and ``stream()``.

LOOPBACK OPT-IN (this example only). The egress guard refuses private and loopback
addresses, so a ``RemoteAgent`` cannot reach ``127.0.0.1`` by default. Like the tests
and the eval, this script calls ``astromesh_a2a.testing.allow_loopback(agent)`` on ITS
agent: only the public-address rule is lifted, for that one instance. It is for local
development and tests only, never production; ``Policy`` has no flag for it, on
purpose. Real remotes are public hosts listed in ``Policy.allowed``: drop
``allow_loopback()``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import threading
import time

from astromesh_a2a import (
    Auth,
    CardSpec,
    Incoming,
    Outgoing,
    Policy,
    RemoteAgent,
    Skill,
    api_key_auth,
    create_app,
)
from astromesh_a2a.testing import allow_loopback

KEY_ENV = "HELLO_A2A_KEY"


async def hello(msg: Incoming) -> Outgoing:
    """The served agent. ``msg`` is untrusted: it was written by another program."""
    if msg.is_reply:  # an answer to our question: still untrusted, never a confirmation
        return Outgoing(answer=f"you said {msg.text!r} to {msg.pending!r}")
    if "delete" in msg.text:
        return Outgoing(needs_confirmation="Deleting is permanent. Reply 'yes' to confirm.")
    return Outgoing(answer=f"hello, {msg.text}!", data=[{"length": len(msg.text)}])


def start_server(port: int) -> tuple[object, threading.Thread]:
    import uvicorn

    card = CardSpec(
        name="hello",
        description="Greets whoever writes to it.",
        version="1.0.0",
        url=f"http://127.0.0.1:{port}",
        skills=[Skill(id="greet", name="Greet", description="Says hello.", tags=("greeting",))],
    )
    app = create_app(hello, card, auth=api_key_auth(KEY_ENV))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    return server, thread


def local_agent(port: int) -> RemoteAgent:
    """A client for the local server. Plain http only because it is local."""
    policy = Policy(allowed=("127.0.0.1",), allow_insecure_http=True, timeout=10.0)
    url = f"http://127.0.0.1:{port}"
    agent = RemoteAgent(url, policy=policy, auth=Auth.api_key(KEY_ENV))
    allow_loopback(agent)  # local development only, never production (see above)
    return agent


async def main(port: int) -> None:
    async with local_agent(port) as agent:
        card = await agent.card()
        print(f"card: {card.name} v{card.version} at {card.interface_url}")

        result = await agent.send("world")
        print(f"send: {result.status} {result.answer!r} data={result.data}")

        result = await agent.send("please delete my notes")
        print(f"send: {result.status}, pending={result.pending!r}")  # nobody said yes yet
        result = await agent.reply(result.task_id, "no")  # the user decides, not the library
        print(f"reply: {result.status} {result.answer!r}")

        async with agent.stream("stream") as events:
            async for event in events:
                print(f"event: {event.kind} {event.payload.get('state', '')}".rstrip())


def run() -> None:
    os.environ.setdefault(KEY_ENV, secrets.token_urlsafe(16))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server, thread = start_server(port)
    try:
        asyncio.run(main(port))
    finally:
        server.should_exit = True
        thread.join(5)


if __name__ == "__main__":
    run()
