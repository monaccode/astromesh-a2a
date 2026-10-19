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
    RemoteCard,
    Result,
    Skill,
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


async def test_parte_no_aceptada_se_descarta(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("mixed")
    assert result.status == "completed"
    assert result.answer == "see file"
    assert result.data == []
    rejections = [e for e in result.trace if e["stage"] == "rejection"]
    assert rejections == [
        {
            "stage": "rejection",
            "ms": rejections[0]["ms"],
            "kind": "file",
            "reason": "part_kind:file",
        }
    ]


async def test_solo_partes_no_aceptadas_rechaza(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(PolicyRejected) as exc:
            await agent.send("file")
    assert exc.value.reason == "part_kind:file"


async def test_pending_conserva_texto_y_descarta_archivo(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        result = await agent.send("askfile")
    assert result.status == "input_required"
    assert result.pending == "which?"
    assert any(e["stage"] == "rejection" for e in result.trace)


@pytest.mark.parametrize(
    "url", ["https://a.example.com/?x=1", "https://a.example.com/#f", "https://a.example.com?"]
)
def test_url_base_con_query_o_fragmento(url):
    with pytest.raises(PolicyRejected) as exc:
        RemoteAgent(url, policy=Policy(allowed=("a.example.com",)))
    assert exc.value.reason == "url"


def test_url_base_y_url_de_card():
    policy = Policy(allowed=("a.example.com",))
    assert RemoteAgent("https://a.example.com", policy=policy)._fetch_url == (
        "https://a.example.com/.well-known/agent-card.json"
    )
    assert RemoteAgent("https://a.example.com/", policy=policy)._fetch_url == (
        "https://a.example.com/.well-known/agent-card.json"
    )
    card = "https://a.example.com/agents/x.json?v=2"
    assert RemoteAgent(card, policy=policy)._fetch_url == card


def _walk(exc):
    seen = []
    while exc is not None:
        assert all(exc is not s for s in seen), "cyclic exception chain"
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


async def test_cadena_de_errores_aciclica(server, loopback, monkeypatch):
    from a2a.client import A2AClientError

    ours = BlockedDestination("Blocked: x")

    async def wrapped(*args):
        try:
            raise ours
        except BlockedDestination as exc:
            raise A2AClientError("wrapped") from exc

    async def plain(*args):
        raise A2AClientError("plain")

    async with RemoteAgent(server.base, policy=_policy()) as agent:
        monkeypatch.setattr(agent, "_send_blocking", wrapped)
        with pytest.raises(BlockedDestination) as exc:
            await agent.send("hello")
        assert exc.value is ours
        _walk(exc.value)

        monkeypatch.setattr(agent, "_send_blocking", plain)
        with pytest.raises(TaskFailed) as exc2:
            await agent.send("hello")
        assert isinstance(exc2.value.__cause__, A2AClientError)
        _walk(exc2.value)


def test_version_no_soportada_mensaje_acotado():
    from a2a.types import AgentCard, AgentInterface

    from astromesh_a2a.client import _select_interface

    card = AgentCard(
        supported_interfaces=[
            AgentInterface(url="https://a/x", protocol_binding="JSONRPC", protocol_version=f"0.{n}")
            for n in range(8)
        ]
    )
    with pytest.raises(UnsupportedVersion) as exc:
        _select_interface(card)
    assert str(exc.value) == "remote speaks A2A 0.0, 0.1, 0.2, 0.3, 0.4, ...; need 1.x"


async def test_data_no_serializable_es_type_error(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(TypeError):
            await agent.send("hello", data={"x": object()})
        with pytest.raises(TypeError):
            await agent.send("hello", data=["not", "a", "dict"])
    assert server.requests == []


async def test_card(server, loopback):
    async with RemoteAgent(server.base + "/.well-known/agent-card.json", policy=_policy()) as a:
        card = await a.card()
    assert isinstance(card, RemoteCard)
    assert card.name == "echo"
    assert card.description == "Echo agent"
    assert card.version == "0.0.1"
    assert card.interface_url == server.base + "/a2a"
    assert card.protocol_version == "1.0"
    assert card.streaming is True
    assert card.has_signatures is False
    assert card.signature_verified is False
    assert card.skills == (
        Skill(
            id="echo",
            name="Echo",
            description="echoes",
            tags=("demo",),
            input_modes=(),  # the remote declared none (card defaults apply)
            output_modes=(),
        ),
    )
    assert [i.protocol_version for i in card.raw.supported_interfaces] == ["1.0"]
    with pytest.raises(AttributeError):
        card.name = "x"  # frozen
    assert "raw" not in repr(card)


async def test_card_firmada_has_signatures_vs_verified(server, loopback):
    url = server.base + "/signed/agent-card.json"
    async with RemoteAgent(url, policy=_policy()) as a:  # signature carried, not checked
        card = await a.card()
    assert card.has_signatures is True
    assert card.signature_verified is False
    policy = _policy(
        require_signature=True, signing_keys={"k1": SIGNING_KEY}, signing_algorithms=["HS256"]
    )
    async with RemoteAgent(server.base + "/signed/agent-card.json", policy=policy) as a:
        card = await a.card()
    assert card.has_signatures is True
    assert card.signature_verified is True


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


async def test_local_error_is_not_translated(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:

        async def boom(*_a, **_k):
            raise RuntimeError("local bug")

        agent._send_blocking = boom
        with pytest.raises(RuntimeError, match="local bug"):
            await agent.send("hi")
