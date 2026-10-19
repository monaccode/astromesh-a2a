"""RemoteAgent: card discovery and blocking send, against the SDK reference server."""

from __future__ import annotations

import pytest
from conftest import API_KEY, SIGNING_KEY

from astromesh_a2a import (
    Auth,
    BlockedDestination,
    Origin,
    Policy,
    PolicyRejected,
    RemoteAgent,
    Result,
    TaskFailed,
    UnsupportedVersion,
)


def _policy(**kw):
    kw.setdefault("allowed", ("127.0.0.1",))
    return Policy(allow_insecure_http=True, timeout=10.0, **kw)


async def test_send_completed(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("hello")
    assert isinstance(result, Result)
    assert result.status == "completed"
    assert result.answer == "echo:hello"
    assert result.data == []
    assert result.task_id
    assert result.context_id
    assert result.pending is None
    assert [e["stage"] for e in result.trace] == ["card", "send", "result"]


async def test_send_data_part_and_context(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        first = await agent.send("data")
        second = await agent.send("again", context_id=first.context_id, data={"q": 1})
    assert first.data == [{"temp": 21}]
    assert first.answer == ""
    assert second.context_id == first.context_id
    # The card is fetched once per RemoteAgent.
    assert [r["path"] for r in server.requests].count("/.well-known/agent-card.json") == 1


async def test_data_part_rechazada_por_politica(server, loopback):
    policy = _policy(accept_parts={"text"})
    async with RemoteAgent(server.base, policy=policy) as agent:
        with pytest.raises(PolicyRejected) as exc:
            await agent.send("data")
    assert exc.value.reason == "part_kind:data"


async def test_card(server, loopback):
    async with RemoteAgent(server.base + "/.well-known/agent-card.json", policy=_policy()) as a:
        card = await a.card()
    assert card.name == "echo"
    assert [i.protocol_version for i in card.supported_interfaces] == ["1.0"]


async def test_card_fuera_de_politica_no_envia(server, loopback):
    with pytest.raises(PolicyRejected) as exc:
        RemoteAgent(server.base, policy=_policy(allowed=("agents.example.com",)))
    assert exc.value.reason == "host_not_allowed"
    with pytest.raises(PolicyRejected):
        RemoteAgent(server.base, policy=Policy(allowed=("127.0.0.1",)))  # http not opted in
    assert server.requests == []


async def test_loopback_bloqueado_sin_fixture(server):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(BlockedDestination):
            await agent.send("hello")
    assert server.requests == []


async def test_interfaz_en_otro_host_requiere_permiso(server, loopback):
    url = server.base + "/other-host/agent-card.json"
    async with RemoteAgent(url, policy=_policy()) as agent:
        with pytest.raises(PolicyRejected) as exc:
            await agent.send("hello")
    assert exc.value.reason == "interface:host_not_allowed"
    assert server.rpc_requests() == []

    server.reset()
    async with RemoteAgent(url, policy=_policy(allowed=("127.0.0.1", "localhost"))) as agent:
        result = await agent.send("hello")
    assert result.answer == "echo:hello"
    assert server.rpc_requests()[0]["headers"]["host"].startswith("localhost:")


async def test_version_header_1_0(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        await agent.send("hello")
    assert server.requests
    assert all(r["headers"].get("a2a-version") == "1.0" for r in server.requests)
    assert [r["method"] for r in server.requests] == ["GET", "POST"]


async def test_metadata_origen_y_traceparent(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        await agent.send("hello")
    async with RemoteAgent(server.base, policy=_policy(), origin=Origin.HUMAN) as agent:
        await agent.send("hi")
    agent_turn, human_turn = server.seen
    meta = agent_turn["message_metadata"]
    assert meta["origin"] == "agent"
    assert human_turn["message_metadata"]["origin"] == "human"
    # Same traceparent in the message metadata and the W3C header.
    header = server.rpc_requests()[0]["headers"]["traceparent"]
    assert meta["traceparent"] == header
    version, trace_id, span_id, flags = header.split("-")
    assert (version, len(trace_id), len(span_id), flags) == ("00", 32, 16, "01")


async def test_auth_api_key_desde_env(server, loopback, monkeypatch):
    monkeypatch.setenv("ECHO_KEY", API_KEY)
    auth = Auth.api_key("ECHO_KEY")
    assert API_KEY not in repr(auth)
    async with RemoteAgent(server.base, policy=_policy(), auth=auth) as agent:
        result = await agent.send("hello")
    rpc = server.rpc_requests()[0]["headers"]
    assert rpc["x-api-key"] == API_KEY
    assert API_KEY not in str(result.trace)
    assert API_KEY not in repr(result)
    # Credentials go to the RPC endpoint, not to the card fetch.
    card_get = next(r for r in server.requests if r["method"] == "GET")
    assert "x-api-key" not in card_get["headers"]


async def test_auth_bearer_y_env_faltante(server, loopback, monkeypatch):
    monkeypatch.setenv("ECHO_TOKEN", "tok-xyz")
    async with RemoteAgent(server.base, policy=_policy(), auth=Auth.bearer("ECHO_TOKEN")) as a:
        await a.send("hello")
    assert server.rpc_requests()[0]["headers"]["authorization"] == "Bearer tok-xyz"

    server.reset()
    monkeypatch.delenv("ECHO_TOKEN")
    async with RemoteAgent(server.base, policy=_policy(), auth=Auth.bearer("ECHO_TOKEN")) as a:
        with pytest.raises(PolicyRejected) as exc:
            await a.send("hello")
    assert exc.value.reason == "auth"
    assert server.rpc_requests() == []


async def test_version_no_soportada(server, loopback):
    async with RemoteAgent(server.base + "/v03/agent-card.json", policy=_policy()) as agent:
        with pytest.raises(UnsupportedVersion):
            await agent.send("hello")
    assert server.rpc_requests() == []


async def test_failed_lanza_task_failed(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(TaskFailed) as exc:
            await agent.send("fail")
    assert "FAILED" in str(exc.value)
    assert "boom" in str(exc.value)


async def test_input_required_devuelve_pending(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("ask")
    assert result.status == "input_required"
    assert result.pending == "which city?"
    assert result.task_id
    assert len(server.rpc_requests()) == 1


async def test_firma_requerida(server, loopback):
    signed = server.base + "/signed/agent-card.json"
    good = _policy(
        require_signature=True, signing_keys={"k1": SIGNING_KEY}, signing_algorithms=["HS256"]
    )
    async with RemoteAgent(signed, policy=good) as agent:
        assert (await agent.send("hello")).answer == "echo:hello"

    server.reset()
    wrong_key = _policy(
        require_signature=True, signing_keys={"k1": "x" * 32}, signing_algorithms=["HS256"]
    )
    for url, policy in [(server.base, good), (signed, wrong_key)]:
        async with RemoteAgent(url, policy=policy) as agent:
            with pytest.raises(PolicyRejected) as exc:
                await agent.send("hello")
        assert exc.value.reason == "signature"
    assert server.rpc_requests() == []


async def test_cerrado(server, loopback):
    agent = RemoteAgent(server.base, policy=_policy())
    await agent.send("hello")
    await agent.aclose()
    await agent.aclose()  # idempotent
    with pytest.raises(RuntimeError):
        await agent.send("hello")
