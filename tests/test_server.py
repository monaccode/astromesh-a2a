"""Server: create_app / serve, against the SDK's official client and our RemoteAgent."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

import astromesh_a2a
from astromesh_a2a import (
    Auth,
    CardSpec,
    Incoming,
    Origin,
    Outgoing,
    Policy,
    RemoteAgent,
    RemoteCard,
    Skill,
    TaskFailed,
)
from astromesh_a2a import server as server_mod
from astromesh_a2a.server import FAILED_TEXT, api_key_auth, create_app, serve

KEY = "srv-key-0123456789"
ENV = "ASTROMESH_A2A_TEST_SERVER_KEY"
SKILL = Skill(id="weather", name="Weather", description="Says the weather", tags=("demo",))


class Recorder:
    """A handler that records what it got. ``confirm``/``transfer`` asks for confirmation."""

    def __init__(self) -> None:
        self.seen: list[Incoming] = []

    async def __call__(self, incoming: Incoming) -> Outgoing:
        self.seen.append(incoming)
        if incoming.text == "boom":
            raise RuntimeError("db password is hunter2 at /srv/internal")
        if incoming.text == "transfer":
            return Outgoing(needs_confirmation="Respond 'yes' to confirm the transfer")
        if incoming.is_reply and incoming.text == "yes":
            # A remote "yes" is just more untrusted input; the handler decides. Here it
            # keeps asking, because only a human on THIS side may confirm.
            return Outgoing(needs_confirmation="A human must confirm this transfer")
        if incoming.text == "slow":
            await asyncio.sleep(5)
        if incoming.text == "data":
            return Outgoing(answer="here", data=[{"temp": 21}])
        return Outgoing(answer=f"got:{incoming.text}", data=incoming.data)


def _card(base: str = "http://testserver", **kw: Any) -> CardSpec:
    return CardSpec(
        name="weather",
        description="Weather agent",
        version="1.2.3",
        url=base,
        skills=[SKILL],
        **kw,
    )


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv(ENV, KEY)


def _asgi(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _rpc(text: str = "hi", *, message: dict | None = None) -> dict[str, Any]:
    message = message or {
        "messageId": "m1",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    return {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}}


HEADERS = {"X-API-Key": KEY, "A2A-Version": "1.0"}


@contextlib.contextmanager
def _live(build: Any) -> Iterator[str]:
    """Run ``build(base) -> app`` under uvicorn on 127.0.0.1; yield the base URL."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    uv = uvicorn.Server(uvicorn.Config(build(base), log_level="warning", lifespan="off"))
    thread = threading.Thread(target=lambda: asyncio.run(uv.serve(sockets=[sock])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.02)
    try:
        yield base
    finally:
        uv.should_exit = True
        thread.join(timeout=5)
        sock.close()


@pytest.fixture
def live(key_env):
    """Our create_app with api_key_auth, live on loopback; yields (base, recorder)."""
    recorder = Recorder()
    with _live(lambda base: create_app(recorder, _card(base), auth=api_key_auth(ENV))) as base:
        yield base, recorder


def _policy() -> Policy:
    return Policy(allowed=("127.0.0.1",), allow_insecure_http=True, timeout=10.0)


# ---- card, startup, auth, limits ----------------------------------------------------------


async def test_card_en_well_known(key_env):
    app = create_app(Recorder(), _card(), auth=api_key_auth(ENV))
    async with _asgi(app) as hx:
        r = await hx.get("/.well-known/agent-card.json")  # public: no key
    assert r.status_code == 200
    card = r.json()
    assert card["name"] == "weather"
    assert card["version"] == "1.2.3"
    assert card["capabilities"]["streaming"] is True
    assert card["supportedInterfaces"] == [
        {"url": "http://testserver/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    ]
    assert card["skills"][0]["id"] == "weather"
    assert card["skills"][0]["tags"] == ["demo"]
    assert card["skills"][0]["inputModes"] == ["text/plain"]
    assert card["securitySchemes"]["apiKey"]["apiKeySecurityScheme"] == {
        "location": "header",
        "name": "X-API-Key",
    }


def test_cardspec_valida():
    with pytest.raises(ValueError, match="http"):
        CardSpec(name="x", description="d", version="1", url="ftp://x")
    with pytest.raises(ValueError, match="name"):
        CardSpec(name="", description="d", version="1", url="https://x")
    with pytest.raises(TypeError):
        CardSpec(name="x", description="d", version="1", url="https://x", skills=["s"])
    with pytest.raises(ValueError, match=r"Skill.id"):
        Skill(id="", name="n", description="d")
    spec = CardSpec(name="x", description="d", version="1", url="https://a.example/sub/")
    assert spec.interface_url == "https://a.example/sub/a2a"


def test_serve_sin_auth_no_arranca(monkeypatch):
    import uvicorn

    runs: list[tuple] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append((app, kw)))
    with pytest.raises(ValueError, match="auth"):
        serve(Recorder(), _card())
    with pytest.raises(ValueError, match="auth"):
        create_app(Recorder(), _card(), auth=None)
    with pytest.raises(ValueError, match="auth"):
        create_app(Recorder(), _card())  # same default as serve(): still refused
    assert runs == []
    serve(Recorder(), _card(), insecure=True, port=8123)
    assert len(runs) == 1
    assert runs[0][1] == {"host": "127.0.0.1", "port": 8123}


async def test_insecure_sirve_sin_auth():
    app = create_app(Recorder(), _card(), auth=None, insecure=True)
    async with _asgi(app) as hx:
        r = await hx.post("/a2a", json=_rpc("hi"), headers={"A2A-Version": "1.0"})
    assert r.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_auth_rechaza_sin_key(key_env, monkeypatch):
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=api_key_auth(ENV))
    async with _asgi(app) as hx:
        none = await hx.post("/a2a", json=_rpc(), headers={"A2A-Version": "1.0"})
        wrong = await hx.post(
            "/a2a", json=_rpc(), headers={"X-API-Key": "nope", "A2A-Version": "1.0"}
        )
        get_card_post = await hx.post("/.well-known/agent-card.json", json={})
        ok = await hx.post("/a2a", json=_rpc(), headers=HEADERS)
        monkeypatch.delenv(ENV)
        unset = await hx.post("/a2a", json=_rpc(), headers=HEADERS)  # fail closed
    for r in (none, wrong, get_card_post, unset):
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized"}
    assert ok.status_code == 200
    assert len(recorder.seen) == 1  # rejected requests never reached the handler


async def test_auth_que_lanza_rechaza_y_identidad(caplog):
    calls: list[str] = []

    async def auth(request):
        calls.append(request.headers.get("x-who", ""))
        if request.headers.get("x-who") == "raise":
            raise RuntimeError("auth backend down")
        return request.headers.get("x-who") or False

    recorder = Recorder()
    app = create_app(recorder, _card(), auth=auth)
    async with _asgi(app) as hx:
        with caplog.at_level(logging.ERROR, logger="astromesh_a2a.server"):
            r = await hx.post("/a2a", json=_rpc(), headers={"x-who": "raise", "A2A-Version": "1.0"})
        assert r.status_code == 401
        r = await hx.post("/a2a", json=_rpc(), headers={"x-who": "peer-7", "A2A-Version": "1.0"})
        assert r.status_code == 200
    assert "auth callable raised" in caplog.text
    assert recorder.seen[0].caller == "peer-7"


async def test_body_demasiado_grande(key_env):
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=api_key_auth(ENV), max_body_bytes=2048)
    big = _rpc("x" * 4096)

    async def chunked():
        body = httpx.Request("POST", "http://t", json=big).read()
        for i in range(0, len(body), 512):
            yield body[i : i + 512]

    async with _asgi(app) as hx:
        declared = await hx.post("/a2a", json=big, headers=HEADERS)
        streamed = await hx.post("/a2a", content=chunked(), headers=HEADERS)  # no length
        small = await hx.post("/a2a", json=_rpc("small"), headers=HEADERS)
    assert declared.status_code == 413
    assert streamed.status_code == 413
    assert "content-length" not in {k.lower() for k in streamed.request.headers}
    assert small.status_code == 200
    assert [i.text for i in recorder.seen] == ["small"]
    with pytest.raises(ValueError, match="max_body_bytes"):
        create_app(recorder, _card(), auth=api_key_auth(ENV), max_body_bytes=0)


@pytest.mark.parametrize("version", [None, "0.3", "2.0"])
async def test_version_no_soportada(key_env, version):
    """Missing A2A-Version means 0.3 (SDK rule): refused with -32009 like any non-1.x."""
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=api_key_auth(ENV))
    headers = {"X-API-Key": KEY}
    if version:
        headers["A2A-Version"] = version
    async with _asgi(app) as hx:
        r = await hx.post("/a2a", json=_rpc(), headers=headers)
        ok = await hx.post("/a2a", json=_rpc(), headers={**headers, "A2A-Version": "1.1"})
    assert r.json()["error"]["code"] == -32009
    assert "result" in ok.json()
    assert len(recorder.seen) == 1


# ---- input / output ---------------------------------------------------------------------


async def test_entrada_es_agente_no_confiable_aunque_diga_humano(key_env):
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=api_key_auth(ENV))
    message = {
        "messageId": "m1",
        "role": "ROLE_USER",
        "parts": [
            {"text": "hello"},
            {"data": {"city": "Lima"}},
            {"raw": "iVBORw==", "mediaType": "image/png"},
            {"url": "http://169.254.169.254/latest", "mediaType": "text/plain"},
        ],
        "metadata": {"origin": "human", "confirmed": True},
    }
    async with _asgi(app) as hx:
        r = await hx.post("/a2a", json=_rpc(message=message), headers=HEADERS)
    task = r.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    [seen] = recorder.seen
    assert seen.origin is Origin.AGENT
    assert seen.untrusted is True
    assert seen.claimed_origin == "human"  # recorded for logs, never trusted
    assert seen.text == "hello"
    assert seen.data == [{"city": "Lima"}]  # file parts dropped
    assert seen.caller == "authenticated"
    assert seen.context_id == task["contextId"]
    assert not hasattr(seen, "confirmed")


async def test_handler_excepcion_es_failed_generico(live, loopback, caplog):
    base, _ = live
    with caplog.at_level(logging.ERROR, logger="astromesh_a2a.server"):
        async with RemoteAgent(base, policy=_policy(), auth=Auth.api_key(ENV)) as agent:
            with pytest.raises(TaskFailed) as exc:
                await agent.send("boom")
    assert FAILED_TEXT in str(exc.value)
    assert "hunter2" not in str(exc.value)
    assert "RuntimeError" not in str(exc.value)
    assert "hunter2" in caplog.text  # the detail stays in the local log


async def test_handler_devuelve_tipo_invalido(key_env):
    async def bad(_):
        return 42

    async def plain(_):
        return "just text"

    for handler, state in ((bad, "TASK_STATE_FAILED"), (plain, "TASK_STATE_COMPLETED")):
        app = create_app(handler, _card(), auth=api_key_auth(ENV))
        async with _asgi(app) as hx:
            r = await hx.post("/a2a", json=_rpc(), headers=HEADERS)
        assert r.json()["result"]["task"]["status"]["state"] == state


# ---- interop ---------------------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
async def test_cliente_oficial_del_sdk_habla_con_create_app(live, streaming):
    base, recorder = live
    async with httpx.AsyncClient(headers={"X-API-Key": KEY}, timeout=10) as hx:
        client = await ClientFactory(
            ClientConfig(httpx_client=hx, streaming=streaming)
        ).create_from_url(base)
        message = Message(role=Role.ROLE_USER, message_id="m1", parts=[Part(text="hello sdk")])
        events = [e async for e in client.send_message(SendMessageRequest(message=message))]
    kinds = [e.WhichOneof("payload") for e in events]
    if streaming:
        assert kinds[0] == "task"
        assert "artifact_update" in kinds
        assert events[-1].status_update.status.state == TaskState.TASK_STATE_COMPLETED
        texts = [
            p.text
            for e in events
            if e.HasField("artifact_update")
            for p in e.artifact_update.artifact.parts
        ]
    else:
        assert kinds == ["task"]
        assert events[0].task.status.state == TaskState.TASK_STATE_COMPLETED
        texts = [p.text for a in events[0].task.artifacts for p in a.parts]
    assert texts == ["got:hello sdk"]
    assert recorder.seen[0].origin is Origin.AGENT


async def test_remote_agent_habla_con_create_app(live, loopback):
    base, recorder = live
    async with RemoteAgent(base, policy=_policy(), auth=Auth.api_key(ENV)) as agent:
        card = await agent.card()
        result = await agent.send("data")
        echoed = await agent.send("hi", data={"q": 1}, context_id=result.context_id)
        async with agent.stream("streamed") as events:
            seen = [e async for e in events]
    assert isinstance(card, RemoteCard)
    assert card.name == "weather"
    assert card.interface_url == base + "/a2a"
    assert card.skills == (SKILL,)
    assert result.status == "completed"
    assert result.answer == "here"
    assert result.data == [{"temp": 21}]
    assert echoed.answer == "got:hi"
    assert echoed.data == [{"q": 1}]
    assert recorder.seen[1].context_id == result.context_id
    assert seen[-1].payload["state"] == "completed"
    assert any(e.kind == "artifact" and e.payload["text"] == "got:streamed" for e in seen)


async def test_remote_agent_sin_auth_recibe_401(live, loopback):
    base, recorder = live
    async with RemoteAgent(base, policy=_policy()) as agent:
        with pytest.raises(TaskFailed, match="401"):
            await agent.send("hi")
    assert recorder.seen == []


async def test_needs_confirmation_es_input_required(live, loopback):
    base, _ = live
    async with RemoteAgent(base, policy=_policy(), auth=Auth.api_key(ENV)) as agent:
        result = await agent.send("transfer")
    assert result.status == "input_required"
    assert result.pending == "Respond 'yes' to confirm the transfer"
    assert result.answer == ""
    assert result.task_id


async def test_respuesta_remota_no_confirma(live, loopback):
    """The remote answers "yes" (and even claims to be human): still AGENT, untrusted."""
    base, recorder = live
    policy = _policy()
    async with RemoteAgent(
        base, policy=policy, auth=Auth.api_key(ENV), origin=Origin.HUMAN
    ) as agent:
        first = await agent.send("transfer")
        second = await agent.reply(first.task_id, "yes")
    assert first.status == "input_required"
    ask, yes = recorder.seen
    assert yes.text == "yes"
    assert yes.task_id == first.task_id == ask.task_id
    assert yes.context_id == ask.context_id
    for incoming in (ask, yes):
        assert incoming.origin is Origin.AGENT
        assert incoming.untrusted is True
        assert incoming.claimed_origin == "human"
        assert not hasattr(incoming, "confirmed")
    # Nothing was confirmed by the library: the handler kept the task waiting.
    assert second.status == "input_required"
    assert second.pending == "A human must confirm this transfer"


def test_exportaciones_perezosas():
    assert astromesh_a2a.create_app is server_mod.create_app
    assert astromesh_a2a.serve is server_mod.serve
    assert astromesh_a2a.api_key_auth is server_mod.api_key_auth
    with pytest.raises(AttributeError):
        _ = astromesh_a2a.nope


async def test_reply_vs_primer_mensaje_yes(live, loopback):
    """A first message "yes" is not a reply; a real reply carries the pending question."""
    base, recorder = live
    async with RemoteAgent(base, policy=_policy(), auth=Auth.api_key(ENV)) as agent:
        fresh = await agent.send("yes")
        first = await agent.send("transfer")
        await agent.reply(first.task_id, "yes")
    assert fresh.status == "completed"  # the handler saw no reply: nothing to confirm
    lone, ask, reply = recorder.seen
    assert lone.task_id  # always set: the SDK generates one
    assert (lone.is_reply, lone.pending) == (False, None)
    assert (ask.is_reply, ask.pending) == (False, None)
    assert reply.is_reply is True
    assert reply.pending == "Respond 'yes' to confirm the transfer"
    assert reply.task_id == ask.task_id


async def test_session_key_separa_identidades():
    async def auth(request):
        return request.headers.get("x-who") or False

    recorder = Recorder()
    app = create_app(recorder, _card(), auth=auth)
    message = {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "hi"}]}
    async with _asgi(app) as hx:
        for who in ("alice", "bob", "a:b"):
            m = {**message, "messageId": f"m-{who}", "contextId": "shared-ctx"}
            r = await hx.post(
                "/a2a", json=_rpc(message=m), headers={"x-who": who, "A2A-Version": "1.0"}
            )
            assert r.status_code == 200
    keys = [i.session_key for i in recorder.seen]
    assert [i.context_id for i in recorder.seen] == ["shared-ctx"] * 3
    assert keys == ["alice:shared-ctx", "bob:shared-ctx", "a%3Ab:shared-ctx"]
    assert len(set(keys)) == 3


async def test_content_length_invalido_es_400(key_env):
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=api_key_auth(ENV), max_body_bytes=2048)
    body = httpx.Request("POST", "http://t", json=_rpc("x")).read()
    async with _asgi(app) as hx:
        for bad in ("abc", "-1", "1e3", ""):
            headers = {**HEADERS, "Content-Type": "application/json", "Content-Length": bad}
            r = await hx.post("/a2a", content=body, headers=headers)
            assert r.status_code == 400, bad
            assert r.json() == {"error": "invalid content-length"}
    assert recorder.seen == []


async def test_otro_caller_no_ve_ni_cancela_la_tarea_ajena():
    async def auth(request):
        return request.headers.get("x-who") or False

    recorder = Recorder()
    app = create_app(recorder, _card(), auth=auth)

    def call(method: str, params: dict) -> dict:
        return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    def as_(who: str) -> dict:
        return {"x-who": who, "A2A-Version": "1.0"}

    async with _asgi(app) as hx:
        r = await hx.post("/a2a", json=_rpc("transfer"), headers=as_("alice"))
        task = r.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"  # still alive
        get = call("GetTask", {"id": task["id"]})
        cancel = call("CancelTask", {"id": task["id"]})
        bob_get = (await hx.post("/a2a", json=get, headers=as_("bob"))).json()
        bob_cancel = (await hx.post("/a2a", json=cancel, headers=as_("bob"))).json()
        reply = {"messageId": "m2", "role": "ROLE_USER", "parts": [{"text": "yes"}]}
        reply["taskId"] = task["id"]
        bob_reply = (await hx.post("/a2a", json=_rpc(message=reply), headers=as_("bob"))).json()
        alice_get = (await hx.post("/a2a", json=get, headers=as_("alice"))).json()
    assert "error" in bob_get
    assert "result" not in bob_get
    assert "error" in bob_cancel
    assert "result" not in bob_cancel
    assert "error" in bob_reply
    assert "result" not in bob_reply
    assert [i.caller for i in recorder.seen] == ["alice"]  # Bob's reply never ran
    # Bob's cancel did nothing: Alice still sees her task waiting for input.
    assert alice_get["result"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"


async def test_session_key_insecure_sin_identidad():
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=None, insecure=True)
    m = {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "hi"}], "contextId": "c1"}
    async with _asgi(app) as hx:
        await hx.post("/a2a", json=_rpc(message=m), headers={"A2A-Version": "1.0"})
    assert recorder.seen[0].session_key == ":c1"
    assert recorder.seen[0].caller is None


async def test_handler_timeout_es_failed(key_env, caplog):
    recorder = Recorder()
    app = create_app(recorder, _card(), auth=api_key_auth(ENV), handler_timeout=0.1)
    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="astromesh_a2a.server"):
        async with _asgi(app) as hx:
            r = await hx.post("/a2a", json=_rpc("slow"), headers=HEADERS)
    assert time.monotonic() - started < 3
    status = r.json()["result"]["task"]["status"]
    assert status["state"] == "TASK_STATE_FAILED"
    assert status["message"]["parts"] == [{"text": FAILED_TEXT}]
    assert "timed out" in caplog.text
    with pytest.raises(ValueError, match="handler_timeout"):
        create_app(recorder, _card(), auth=api_key_auth(ENV), handler_timeout=0)


def test_lifespan_cierra_handler_y_task_store(key_env, monkeypatch):
    from a2a.server.tasks import InMemoryTaskStore
    from starlette.testclient import TestClient

    closed: list[bool] = []

    async def fake_aclose(self):
        closed.append(True)

    monkeypatch.setattr(server_mod.DefaultRequestHandler, "aclose", fake_aclose)
    saved: list[str] = []

    class Store(InMemoryTaskStore):
        async def save(self, task, context):
            saved.append(task.id)
            await super().save(task, context)

    store = Store()
    app = create_app(Recorder(), _card(), auth=api_key_auth(ENV), task_store=store)
    with TestClient(app) as tc:
        r = tc.post("/a2a", json=_rpc("hi"), headers=HEADERS)
        task_id = r.json()["result"]["task"]["id"]
        assert closed == []
    assert closed == [True]
    assert task_id in saved  # our store was used
