"""RemoteAgent: streaming, input-required, reply, cancel, budgets and card TTL."""

from __future__ import annotations

import json
import time

import pytest

from astromesh_a2a import (
    A2ATimeout,
    BlockedDestination,
    Budget,
    Event,
    Policy,
    PolicyRejected,
    RemoteAgent,
    TaskFailed,
    UnsupportedVersion,
)
from astromesh_a2a.net import PinnedTransport, _BoundedBody


def _policy(**kw):
    kw.setdefault("allowed", ("127.0.0.1",))
    kw.setdefault("timeout", 10.0)
    return Policy(allow_insecure_http=True, **kw)


async def _collect(stream) -> list[Event]:
    return [event async for event in stream]


# ---- stream ------------------------------------------------------------------------


async def test_stream_artefactos_en_orden(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        stream = agent.stream("multi")
        events = await _collect(stream)
    assert all(isinstance(e, Event) for e in events)
    artifacts = [e.payload["text"] for e in events if e.kind == "artifact"]
    assert artifacts == ["one", "two", "three"]
    assert events[0].kind == "status"
    assert events[0].payload["state"] == "submitted"
    last = events[-1]
    assert last.kind == "status"
    assert last.payload["state"] == "completed"
    assert last.payload["final"] is True
    assert all(e.payload["untrusted"] is True for e in events)
    assert len({e.payload["task_id"] for e in events}) == 1
    json.dumps([e.payload for e in events])  # plain data, no protobuf
    stages = [e["stage"] for e in stream.trace]
    assert stages[:2] == ["card", "send"]
    assert stages.count("artifact") == 3
    assert [r["path"] for r in server.rpc_requests()] == ["/a2a"]


async def test_stream_descarta_partes_no_aceptadas(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        stream = agent.stream("mixed")
        events = await _collect(stream)
    (artifact,) = [e for e in events if e.kind == "artifact"]
    assert artifact.payload["text"] == "see file"
    assert artifact.payload["data"] == []
    assert [e["reason"] for e in stream.trace if e["stage"] == "rejection"] == ["part_kind:file"]


@pytest.mark.parametrize(
    ("policy_kw", "why"),
    [({"max_events": 5}, "events"), ({"timeout": 0.3}, "s")],
    ids=["max_events", "timeout"],
)
async def test_stream_infinito_corta(server, loopback, policy_kw, why):
    started = time.monotonic()
    seen = []
    async with RemoteAgent(server.base, policy=_policy(**policy_kw)) as agent:
        with pytest.raises(A2ATimeout) as exc:
            seen.extend([e async for e in agent.stream("forever")])
        # The agent is still usable after a cut stream.
        assert (await agent.send("hi")).answer == "echo:hi"
    assert why in str(exc.value)
    assert time.monotonic() - started < 3.0  # far less than the ~4 s the remote would take
    assert seen == []  # the stream raised: the list was never filled


async def test_stream_parada_temprana_cierra(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        async with agent.stream("forever") as events:
            async for event in events:
                assert event.kind == "status"
                break
        assert (await agent.send("hi")).answer == "echo:hi"


# ---- body caps on SSE ----------------------------------------------------------------


async def test_stream_largo_usa_tope_de_stream(server, loopback):
    # 12 x 1000-byte artifacts: the stream is well over max_bytes, each event is under it.
    async with RemoteAgent(server.base, policy=_policy(max_bytes=4096)) as agent:
        events = await _collect(agent.stream("big"))
    assert [len(e.payload["text"]) for e in events if e.kind == "artifact"] == [1000] * 12


async def test_stream_supera_tope_total(server, loopback):
    policy = _policy(max_bytes=4096, max_stream_bytes=8192)
    async with RemoteAgent(server.base, policy=policy) as agent:
        with pytest.raises(BlockedDestination) as exc:
            await _collect(agent.stream("big"))
    assert "8192" in str(exc.value)


async def test_evento_supera_tope_por_evento(server, loopback):
    async with RemoteAgent(server.base, policy=_policy(max_bytes=4096)) as agent:
        with pytest.raises(BlockedDestination) as exc:
            await _collect(agent.stream("huge"))
    assert "event exceeds 4096" in str(exc.value)


async def test_tope_por_evento_entre_chunks():
    class _Chunks:
        def __init__(self, chunks):
            self.chunks = chunks

        async def __aiter__(self):
            for c in self.chunks:
                yield c

        async def aclose(self):
            pass

    def body(chunks):
        return _BoundedBody(_Chunks(chunks), 10_000, PinnedTransport(), max_event_bytes=12)

    # CRLF framing split across chunks: each event (10 bytes + blank line) fits.
    ok = [b"data: ab\r", b"\n\r", b"\ndata: cd\r\n", b"\r\n"]
    assert b"".join([c async for c in body(ok)]) == b"".join(ok)
    with pytest.raises(BlockedDestination):
        _ = [c async for c in body([b"data: abc", b"defgh\r\n\r\n"])]


# ---- input required / reply / no auto-confirm -------------------------------------


async def test_input_required_devuelve_pending(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("ask")
    assert result.status == "input_required"
    assert result.pending == "which city?"
    assert result.answer == ""
    assert result.task_id


async def test_reply_continua_misma_tarea(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        first = await agent.send("ask")
        second = await agent.reply(first.task_id, "paris")
    assert second.status == "completed"
    assert second.answer == "echo:paris"
    assert second.task_id == first.task_id
    assert second.context_id == first.context_id
    assert server.seen[-1]["text"] == "paris"
    assert server.seen[-1]["message_metadata"]["origin"] == "agent"
    (send,) = [e for e in second.trace if e["stage"] == "send"]
    assert send["task_id"] == first.task_id


async def test_reply_valida_task_id(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(ValueError, match="task_id"):
            await agent.reply("", "x")
    assert server.requests == []


async def test_no_autoconfirma(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("confirm")
        assert result.status == "input_required"
        assert "yes" in result.pending
        assert len(server.rpc_requests()) == 1  # nothing answered the question

        events = await _collect(agent.stream("confirm"))
        assert events[-1].payload["state"] == "input_required"
        assert events[-1].payload["final"] is True
        assert "yes" in events[-1].payload["text"]
        assert len(server.rpc_requests()) == 2
    assert [s["text"] for s in server.seen] == ["confirm", "confirm"]


# ---- cancel -------------------------------------------------------------------------


async def test_cancel(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        pending = await agent.send("ask")
        assert await agent.cancel(pending.task_id) is None
        with pytest.raises(TaskFailed):  # already canceled: not cancelable again
            await agent.cancel(pending.task_id)
        done = await agent.send("hi")
        with pytest.raises(TaskFailed):
            await agent.cancel(done.task_id)


async def test_cancel_corta_stream_abierto(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        states = []
        async for event in agent.stream("forever"):
            states.append(event.payload["state"])
            if event.payload["state"] == "working" and len(states) == 2:
                await agent.cancel(event.payload["task_id"])
    assert states[-1] == "canceled"
    assert len(states) < 50


# ---- budgets ------------------------------------------------------------------------


async def test_presupuesto_corta_antes_de_enviar(server, loopback):
    policy = _policy(budget=Budget(max_calls=1))
    async with RemoteAgent(server.base, policy=policy) as agent:
        first = await agent.send("ask")
        sent = len(server.requests)
        for call in (
            lambda: agent.send("again"),
            lambda: agent.reply(first.task_id, "paris"),
            lambda: agent.cancel(first.task_id),
            lambda: _collect(agent.stream("again")),
        ):
            with pytest.raises(PolicyRejected) as exc:
                await call()
            assert exc.value.reason == "budget"
        assert len(server.requests) == sent  # not even the card left


async def test_presupuesto_cero_ni_siquiera_la_card(server, loopback):
    async with RemoteAgent(server.base, policy=_policy(budget=Budget(max_calls=0))) as agent:
        with pytest.raises(PolicyRejected):
            await agent.send("hi")
    assert server.requests == []


async def test_presupuesto_de_segundos(server, loopback):
    policy = _policy(budget=Budget(max_seconds=0.3))
    async with RemoteAgent(server.base, policy=policy) as agent:
        started = time.monotonic()
        with pytest.raises(A2ATimeout):  # the call is capped at the seconds left
            await _collect(agent.stream("forever"))
        assert time.monotonic() - started < 2.0
        sent = len(server.requests)
        with pytest.raises(PolicyRejected) as exc:
            await agent.send("hi")
    assert exc.value.reason == "budget"
    assert len(server.requests) == sent


# ---- card TTL -----------------------------------------------------------------------


async def test_card_ttl_refresca_y_revalida(server, loopback):
    url = server.base + "/ttl/agent-card.json"
    async with RemoteAgent(url, policy=_policy(card_ttl=0.0)) as agent:
        assert (await agent.send("hi")).answer == "echo:hi"
        assert (await agent.send("hi")).answer == "echo:hi"
        server.ttl_version = "0.3"
        with pytest.raises(UnsupportedVersion):
            await agent.send("hi")
        with pytest.raises(UnsupportedVersion):  # no stale card left behind
            await agent.send("hi")
    paths = [r["path"] for r in server.requests]
    assert paths.count("/ttl/agent-card.json") == 4
    assert paths.count("/a2a") == 2


def test_card_ttl_negativo():
    with pytest.raises(ValueError, match="card_ttl"):
        Policy(card_ttl=-1)
