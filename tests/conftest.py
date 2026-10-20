"""Shared fixtures: the a2a-sdk reference server, in process (ADR 0001 §7).

The server runs under uvicorn on 127.0.0.1 in a background thread (its own event loop),
once per test session. It serves several cards from one app:

- ``/.well-known/agent-card.json``: the echo agent, JSON-RPC 1.0 at ``/a2a``.
- ``/v03/agent-card.json``: same agent, but its only interface says protocol 0.3.
- ``/other-host/agent-card.json``: interface on ``localhost`` (card is on 127.0.0.1).
- ``/signed/agent-card.json``: the echo card signed with ``SIGNING_KEY`` (HS256, kid k1).
- ``/ttl/agent-card.json``: the echo card, its protocol version and RPC base read from
  ``server.ttl_version``/``server.ttl_rpc_base`` on every fetch (to test card refresh).

``server.connections()`` counts the TCP connections uvicorn has open, so tests can prove
the client really closed a stream (an abandoned, half-read stream keeps its connection).

``server.requests`` records every HTTP request (method, path, headers) and
``server.seen`` what the executor received (text, message/request metadata), so tests
can assert what left the client — and what did not.

Loopback is blocked by the egress guard, so tests that talk to this server use the
``loopback`` fixture: it applies ``astromesh_a2a.testing.allow_loopback`` to every
``RemoteAgent`` the test builds, whose clients are then ``net.safe_client(...,
allow_private=True)``. That keeps the real ``PinnedTransport`` (size cap, no redirects,
one host per client) and only lifts the public-address rule. ``Policy`` has no flag
that turns it on.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from a2a.helpers import new_data_part, new_task, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    TaskState,
)
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette

API_KEY = "k-test-0123456789"
FOREVER_TICKS = 400
SIGNING_KEY = "s" * 32


@dataclass
class RefServer:
    base: str
    requests: list[dict[str, Any]] = field(default_factory=list)
    seen: list[dict[str, Any]] = field(default_factory=list)
    ttl_version: str = "1.0"
    ttl_rpc_base: str | None = None
    uv: Any = None

    def connections(self) -> int:
        return len(self.uv.server_state.connections)

    def reset(self) -> None:
        self.requests.clear()
        self.seen.clear()
        self.ttl_version = "1.0"
        self.ttl_rpc_base = None

    def rpc_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]


class EchoExecutor(AgentExecutor):
    """Echo agent; some inputs pick a behavior.

    ``fail`` → FAILED; ``ask``/``confirm`` → INPUT_REQUIRED; ``data`` → data artifact;
    ``multi`` → three artifacts in order; ``forever`` → WORKING ticks (bounded at
    ``FOREVER_TICKS`` so the session server never spins for good); ``big`` → many 1000-byte
    artifacts; ``huge`` → one 6000-byte artifact; anything else → ``echo:<text>``.
    """

    def __init__(self, server: RefServer):
        self.server = server

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        self.server.seen.append(
            {
                "text": text,
                "message_metadata": MessageToDict(context.message.metadata),
                "request_metadata": dict(context.metadata),
            }
        )
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message],
                )
            )
        up = TaskUpdater(event_queue, context.task_id, context.context_id)
        await up.start_work()
        if text == "fail":
            await up.failed(up.new_agent_message([new_text_part("boom")]))
            return
        if text == "ask":
            await up.requires_input(up.new_agent_message([new_text_part("which city?")]))
            return
        if text == "confirm":
            question = "Respond 'yes' to confirm the transfer"
            await up.requires_input(up.new_agent_message([new_text_part(question)]))
            return
        if text == "forever":
            for i in range(FOREVER_TICKS):
                await up.update_status(
                    TaskState.TASK_STATE_WORKING, up.new_agent_message([new_text_part(f"t{i}")])
                )
                await asyncio.sleep(0.01)
            await up.complete()
            return
        if text == "multi":
            for word in ("one", "two", "three"):
                await up.add_artifact([new_text_part(word)], name=word)
            await up.complete()
            return
        if text in ("big", "huge"):
            sizes = [1000] * 12 if text == "big" else [6000]
            for n in sizes:
                await up.add_artifact([new_text_part("x" * n)], name="chunk")
            await up.complete()
            return
        file_part = Part(raw=b"\x89PNG", media_type="image/png", filename="x.png")
        if text == "askfile":
            await up.requires_input(up.new_agent_message([file_part, new_text_part("which?")]))
            return
        if text == "filethen":
            await up.add_artifact([file_part], name="file")
            await up.add_artifact([new_text_part("ok")], name="answer")
            await up.complete()
            return
        if text == "mixed":
            await up.add_artifact([new_text_part("see file"), file_part], name="answer")
        elif text == "file":
            await up.add_artifact([file_part], name="answer")
        elif text == "data":
            await up.add_artifact([new_data_part({"temp": 21})], name="answer")
        else:
            await up.add_artifact([new_text_part(f"echo:{text}")], name="answer")
        await up.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def _card(base: str, *, version: str = "1.0", rpc_base: str | None = None) -> AgentCard:
    return AgentCard(
        name="echo",
        description="Echo agent",
        version="0.0.1",
        supported_interfaces=[
            AgentInterface(
                url=(rpc_base or base) + "/a2a",
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=version,
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="echo", name="Echo", description="echoes", tags=["demo"])],
    )


def _signed(card: AgentCard) -> AgentCard:
    from a2a.utils.signing import create_agent_card_signer

    signer = create_agent_card_signer(
        SIGNING_KEY, {"kid": "k1", "alg": "HS256", "jku": None, "typ": "JOSE"}
    )
    return signer(card)


class _Recorder:
    """Pure ASGI middleware: records every HTTP request before the app sees it."""

    def __init__(self, app: Any, server: RefServer):
        self.app = app
        self.server = server

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            self.server.requests.append(
                {"method": scope["method"], "path": scope["path"], "headers": headers}
            )
        await self.app(scope, receive, send)


def build_app(server: RefServer) -> Any:
    base = server.base
    card = _card(base)
    port = base.rsplit(":", 1)[1]

    async def _ttl_card(_: AgentCard) -> AgentCard:
        return _card(base, version=server.ttl_version, rpc_base=server.ttl_rpc_base)

    handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(server), task_store=InMemoryTaskStore(), agent_card=card
    )
    routes = [
        *create_agent_card_routes(card),
        *create_agent_card_routes(_card(base, version="0.3"), card_url="/v03/agent-card.json"),
        *create_agent_card_routes(
            _card(base, rpc_base=f"http://localhost:{port}"),
            card_url="/other-host/agent-card.json",
        ),
        *create_agent_card_routes(_signed(_card(base)), card_url="/signed/agent-card.json"),
        *create_agent_card_routes(card, card_modifier=_ttl_card, card_url="/ttl/agent-card.json"),
        *create_jsonrpc_routes(handler, rpc_url="/a2a"),
    ]
    return _Recorder(Starlette(routes=routes), server)


@pytest.fixture(scope="session")
def ref_server():
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = RefServer(base=f"http://127.0.0.1:{port}")
    uv = uvicorn.Server(uvicorn.Config(build_app(server), log_level="warning", lifespan="off"))
    server.uv = uv
    thread = threading.Thread(target=lambda: asyncio.run(uv.serve(sockets=[sock])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("reference server did not start")
        time.sleep(0.02)
    yield server
    uv.should_exit = True
    thread.join(timeout=5)
    sock.close()


@pytest.fixture
def server(ref_server):
    ref_server.reset()
    return ref_server


@pytest.fixture
def loopback(monkeypatch):
    """Every RemoteAgent built in this test may reach 127.0.0.1/localhost (see module doc)."""
    from astromesh_a2a.client import RemoteAgent
    from astromesh_a2a.testing import allow_loopback

    original = RemoteAgent.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        allow_loopback(self)

    monkeypatch.setattr(RemoteAgent, "__init__", init)
