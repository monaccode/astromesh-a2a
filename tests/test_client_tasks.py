"""RemoteAgent task API (submit, get_task, list_tasks, subscribe) and reply(context_id=),
against the SDK reference server."""

from __future__ import annotations

import asyncio
import time

import pytest
from conftest import TICKS

from astromesh_a2a import (
    A2ATimeout,
    BlockedDestination,
    Budget,
    Policy,
    PolicyRejected,
    RemoteAgent,
    Result,
    TaskFailed,
    TaskPage,
)


def _policy(**kw):
    kw.setdefault("allowed", ("127.0.0.1",))
    kw.setdefault("timeout", 10.0)
    return Policy(allow_insecure_http=True, **kw)


async def _until_done(agent: RemoteAgent, task_id: str) -> Result:
    deadline = time.monotonic() + 10
    while True:
        result = await agent.get_task(task_id)
        if result.status not in ("submitted", "working"):
            return result
        assert time.monotonic() < deadline, "task never finished"
        await asyncio.sleep(0.05)


# ---- reply(context_id=) -------------------------------------------------------------


async def test_reply_context_id_explicito_llega_al_servidor(server, loopback, monkeypatch):
    """A fresh RemoteAgent has no remembered context: the explicit one is sent."""
    async with RemoteAgent(server.base, policy=_policy()) as first:
        asked = await first.send("ask")
    assert asked.status == "input_required"
    sent: list = []
    original = RemoteAgent._message_request

    def spy(self, *args, **kwargs):
        request = original(self, *args, **kwargs)
        sent.append(request.message)
        return request

    monkeypatch.setattr(RemoteAgent, "_message_request", spy)
    async with RemoteAgent(server.base, policy=_policy()) as fresh:
        assert fresh._contexts == {}
        result = await fresh.reply(asked.task_id, "Lima", context_id=asked.context_id)
        with pytest.raises(ValueError, match="context_id"):
            await fresh.reply(asked.task_id, "x", context_id="")
    assert sent[0].task_id == asked.task_id
    assert sent[0].context_id == asked.context_id
    assert result.status == "completed"
    assert result.answer == "echo:Lima"
    assert result.task_id == asked.task_id
    assert result.context_id == asked.context_id


async def test_reply_sin_context_id_usa_el_recordado(server, loopback, monkeypatch):
    sent: list = []
    original = RemoteAgent._message_request

    def spy(self, *args, **kwargs):
        request = original(self, *args, **kwargs)
        sent.append(request.message)
        return request

    monkeypatch.setattr(RemoteAgent, "_message_request", spy)
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        asked = await agent.send("ask")
        await agent.reply(asked.task_id, "Lima")
    assert sent[1].context_id == asked.context_id


# ---- submit / get_task --------------------------------------------------------------


async def test_submit_no_espera_y_get_task_ve_el_final(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        submitted = await agent.submit("tick")
        assert submitted.status in ("submitted", "working")
        assert submitted.task_id
        assert submitted.context_id
        assert submitted.pending is None
        assert [e["stage"] for e in submitted.trace] == ["card", "send", "result"]
        assert submitted.trace[1]["return_immediately"] is True
        done = await _until_done(agent, submitted.task_id)
    assert done.status == "completed"
    assert done.answer == "\n".join(f"t{i}" for i in range(TICKS))
    assert done.task_id == submitted.task_id
    assert [e["stage"] for e in done.trace] == ["card", "send", "result"]
    assert done.trace[1]["method"] == "GetTask"


async def test_submit_con_contexto_y_data(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        first = await agent.send("hello")
        submitted = await agent.submit("again", context_id=first.context_id, data={"q": 1})
        done = await _until_done(agent, submitted.task_id)
    assert submitted.context_id == first.context_id
    assert done.answer == "echo:again"


async def test_get_task_estados_no_lanzan(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(TaskFailed):  # send() keeps raising on a failed task
            await agent.send("fail")
        failed = await agent.submit("fail")
        failed = await _until_done(agent, failed.task_id)
        asked = await agent.submit("ask")
        asked = await _until_done(agent, asked.task_id)
    assert (failed.status, failed.answer) == ("failed", "boom")
    assert (asked.status, asked.pending, asked.answer) == ("input_required", "which city?", "")


async def test_get_task_desconocida_es_task_failed_remoto(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(TaskFailed) as exc:
            await agent.get_task("no-such-task")
        with pytest.raises(ValueError, match="task_id"):
            await agent.get_task("")
    assert exc.value.remote is True


async def test_get_task_aplica_la_politica_de_partes(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        done = await agent.send("data")
    async with RemoteAgent(server.base, policy=_policy(accept_parts={"text"})) as strict:
        with pytest.raises(PolicyRejected) as exc:
            await strict.get_task(done.task_id)
    assert exc.value.reason == "part_kind:data"


async def test_task_api_cobra_presupuesto(server, loopback):
    policy = _policy(budget=Budget(max_calls=2))
    async with RemoteAgent(server.base, policy=policy) as agent:
        submitted = await agent.submit("hello")
        await agent.get_task(submitted.task_id)
        before = len(server.rpc_requests())
        with pytest.raises(PolicyRejected) as exc:
            await agent.list_tasks()
        with pytest.raises(PolicyRejected):
            await agent.get_task(submitted.task_id)
        with pytest.raises(PolicyRejected):
            await _drain(agent.subscribe(submitted.task_id))
    assert exc.value.reason == "budget"
    assert len(server.rpc_requests()) == before  # nothing left


async def test_traceparent_en_get_task(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        done = await agent.send("hello")
        server.requests.clear()
        result = await agent.get_task(done.task_id)
    (request,) = server.rpc_requests()
    version, trace_id, span_id, flags = request["headers"]["traceparent"].split("-")
    assert (version, len(trace_id), len(span_id), flags) == ("00", 32, 16, "01")
    assert result.status == "completed"


# ---- list_tasks ---------------------------------------------------------------------


async def test_list_tasks_filtra_y_pagina(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        first = await agent.send("one")
        ctx = first.context_id
        await agent.send("two", context_id=ctx)
        await agent.send("ask", context_id=ctx)
        page = await agent.list_tasks(context_id=ctx)
        waiting = await agent.list_tasks(context_id=ctx, status="input_required")
        p1 = await agent.list_tasks(context_id=ctx, page_size=2)
        p2 = await agent.list_tasks(context_id=ctx, page_size=2, page_token=p1.next_page_token)
    assert isinstance(page, TaskPage)
    assert {r.answer for r in page.tasks} == {"echo:one", "echo:two", ""}
    assert all(r.context_id == ctx and r.trace == [] for r in page.tasks)
    assert page.next_page_token is None
    assert page.total_size == 3
    assert page.trace[1]["method"] == "ListTasks"
    assert [(r.status, r.pending) for r in waiting.tasks] == [("input_required", "which city?")]
    assert len(p1.tasks) == 2
    assert p1.next_page_token
    assert len(p2.tasks) == 1
    assert p2.next_page_token is None
    assert {r.task_id for r in p1.tasks + p2.tasks} == {r.task_id for r in page.tasks}


async def test_list_tasks_descarta_partes_rechazadas_sin_romper_la_pagina(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        done = await agent.send("data")
    async with RemoteAgent(server.base, policy=_policy(accept_parts={"text"})) as strict:
        page = await strict.list_tasks(context_id=done.context_id)
    (listed,) = page.tasks
    assert (listed.status, listed.answer, listed.data) == ("completed", "", [])
    assert any(e["stage"] == "rejection" for e in page.trace)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_size": 0},
        {"page_size": 101},
        {"page_size": True},
        {"page_size": "5"},
        {"status": "done"},
        {"context_id": ""},
        {"page_token": ""},
    ],
)
async def test_list_tasks_valida_argumentos(server, loopback, kwargs):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(ValueError, match=next(iter(kwargs))):
            await agent.list_tasks(**kwargs)
    assert server.requests == []  # refused before anything left


# ---- subscribe ----------------------------------------------------------------------


async def _drain(stream) -> list:
    async with stream as events:
        return [e async for e in events]


async def test_submit_y_subscribe(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        submitted = await agent.submit("tick")
        stream = agent.subscribe(submitted.task_id)
        events = await _drain(stream)
    assert events[-1].kind == "status"
    assert events[-1].payload["state"] == "completed"
    assert events[-1].payload["final"] is True
    texts = [e.payload["text"] for e in events if e.kind == "artifact"]
    assert texts == [f"t{i}" for i in range(TICKS)]  # the task so far, then the rest
    assert all(e.payload["untrusted"] is True for e in events)
    assert all(e.payload["task_id"] == submitted.task_id for e in events)
    assert next(e for e in stream.trace if e["stage"] == "send")["method"] == "SubscribeToTask"


async def test_subscribe_a_tarea_terminada_es_task_failed(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        done = await agent.send("hello")
        with pytest.raises(TaskFailed) as exc:
            await _drain(agent.subscribe(done.task_id))
        with pytest.raises(TaskFailed):
            await _drain(agent.subscribe("no-such-task"))
        with pytest.raises(ValueError, match="task_id"):
            agent.subscribe("")
    assert exc.value.remote is True


async def test_subscribe_a_tarea_esperando_input_termina(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        asked = await agent.send("ask")
        events = await _drain(agent.subscribe(asked.task_id))
    assert events[-1].payload["state"] == "input_required"
    assert events[-1].payload["text"] == "which city?"


async def test_subscribe_respeta_timeout(server, loopback):
    async with RemoteAgent(server.base, policy=_policy(timeout=0.25)) as agent:
        submitted = await agent.submit("tick")  # runs ~0.5 s
        started = time.monotonic()
        with pytest.raises(A2ATimeout):
            await _drain(agent.subscribe(submitted.task_id))
    assert time.monotonic() - started < 3


async def test_subscribe_respeta_max_events(server, loopback):
    async with RemoteAgent(server.base, policy=_policy(max_events=3)) as agent:
        submitted = await agent.submit("forever")
        with pytest.raises(A2ATimeout, match="3 events"):
            await _drain(agent.subscribe(submitted.task_id))


async def test_subscribe_sin_streaming_en_la_card(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        submitted = await agent.submit("tick")
        agent._card.capabilities.streaming = False  # as if the remote did not stream
        with pytest.raises(TaskFailed, match="streaming"):
            await _drain(agent.subscribe(submitted.task_id))


# ---- review fixes: id validation, body/stream caps, listing hygiene ---------------------

BAD_IDS = ["x" * 257, "a" + chr(10) + "b", "tab" + chr(9) + "here", "nul" + chr(0)]


@pytest.mark.parametrize("bad", BAD_IDS)
async def test_ids_invalidos_no_salen(server, loopback, bad):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(ValueError, match="context_id"):
            await agent.send("hi", context_id=bad)
        with pytest.raises(ValueError, match="context_id"):
            await agent.submit("hi", context_id=bad)
        with pytest.raises(ValueError, match="context_id"):
            agent.stream("hi", context_id=bad)
        with pytest.raises(ValueError, match="context_id"):
            await agent.reply("t1", "hi", context_id=bad)
        with pytest.raises(ValueError, match="context_id"):
            await agent.list_tasks(context_id=bad)
        with pytest.raises(ValueError, match="page_token"):
            await agent.list_tasks(page_token=bad)
        with pytest.raises(ValueError, match="task_id"):
            await agent.reply(bad, "hi")
        with pytest.raises(ValueError, match="task_id"):
            await agent.get_task(bad)
        with pytest.raises(ValueError, match="task_id"):
            await agent.cancel(bad)
        with pytest.raises(ValueError, match="task_id"):
            agent.subscribe(bad)
    assert server.requests == []  # nothing left, not even the card fetch


async def test_send_context_id_vacio_sigue_valiendo(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("hello", context_id="")
        longest = await agent.send("hello", context_id="c" * 256)
    assert result.status == "completed"
    assert longest.context_id == "c" * 256


async def test_get_task_y_list_tasks_respetan_max_bytes(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        done = await agent.send("huge")  # one 6000-byte artifact
    async with RemoteAgent(server.base, policy=_policy(max_bytes=4096)) as small:
        with pytest.raises(BlockedDestination, match="4096"):
            await small.get_task(done.task_id)
        with pytest.raises(BlockedDestination, match="4096"):
            await small.list_tasks(context_id=done.context_id)


async def test_subscribe_respeta_max_stream_bytes(server, loopback):
    policy = _policy(max_bytes=4096, max_stream_bytes=8192)
    async with RemoteAgent(server.base, policy=policy) as agent:
        submitted = await agent.submit("bigslow")
        with pytest.raises(BlockedDestination, match="8192"):
            await _drain(agent.subscribe(submitted.task_id))


async def test_list_tasks_no_toca_el_mapa_de_contextos(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        asked = await agent.send("ask")
        agent._contexts.clear()
        page = await agent.list_tasks(context_id=asked.context_id)
        assert [r.task_id for r in page.tasks] == [asked.task_id]
        assert agent._contexts == {}  # listing never evicts or adds pending contexts
        await agent.get_task(asked.task_id)
        assert agent._contexts == {asked.task_id: asked.context_id}


async def test_list_tasks_descarta_estado_desconocido(server, loopback, monkeypatch):
    from a2a.types import TaskState

    async with RemoteAgent(server.base, policy=_policy()) as agent:
        first = await agent.send("one")
        await agent.send("two", context_id=first.context_id)
        client = agent._sdk_client(streaming=False)
        original = client.list_tasks

        async def corrupt(request, *, context=None):
            page = await original(request, context=context)
            page.tasks[0].status.state = TaskState.TASK_STATE_UNSPECIFIED
            return page

        monkeypatch.setattr(client, "list_tasks", corrupt)
        page = await agent.list_tasks(context_id=first.context_id)
    assert len(page.tasks) == 1
    assert any(e["stage"] == "rejection" and e.get("reason") == "unknown_state" for e in page.trace)
