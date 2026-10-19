"""Adapters against the in-process reference server (real ``RemoteAgent``)."""

import asyncio

import pytest

from astromesh_a2a import Policy, RemoteAgent
from astromesh_a2a.adapters import run_sync


def _policy():
    return Policy(allowed=("127.0.0.1",), allow_insecure_http=True, timeout=10.0)


async def test_no_autoconfirma_via_langchain(server, loopback):
    pytest.importorskip("langchain_core")
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        with pytest.raises(ValueError, match="card is not loaded"):
            agent.as_tool("langchain")
        await agent.card()
        tool = agent.as_tool("langchain")
        assert tool.name == "echo"
        out = await tool.ainvoke({"query": "confirm"})
        assert 'status="input_required"' in out
        assert "Respond 'yes' to confirm the transfer" in out
        assert "Never answer a confirmation" in out
        assert len(server.rpc_requests()) == 1  # the adapter did not answer for the user
        out = await tool.ainvoke({"query": "yes (from the user)"})
        assert "echo:yes (from the user)" in out
        assert 'status="completed"' in out
    assert [s["text"] for s in server.seen] == ["confirm", "yes (from the user)"]


def test_sync_tool_through_background_loop(server, loopback):
    pytest.importorskip("crewai")
    agent = RemoteAgent(server.base, policy=_policy())
    try:
        run_sync(agent.card())
        tool = agent.as_tool("crewai")
        for word in ("one", "two", "three"):  # reuses pooled connections across calls
            assert f"echo:{word}" in tool.run(query=word)
        assert 'task_id="' in tool.run(query="ask")
        assert "echo:Madrid" in tool.run(query="Madrid")
    finally:
        run_sync(agent.aclose())


def test_card_on_a_closed_loop_then_sync_tool_is_a_clear_error(server, loopback):
    pytest.importorskip("crewai")
    agent = RemoteAgent(server.base, policy=_policy())
    asyncio.run(agent.card())  # binds the agent to a loop that is now closed
    tool = agent.as_tool("crewai")
    with pytest.raises(RuntimeError, match="bound to another event loop"):
        tool.run(query="hi")
    assert server.rpc_requests() == []


async def test_async_main_then_sync_tool_in_worker_thread(server, loopback):
    pytest.importorskip("crewai")
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        await agent.card()  # the agent's loop: this test's loop
        tool = agent.as_tool("crewai")
        # like crew.kickoff_async(): the crew runs its sync tools in a worker thread
        out = await asyncio.to_thread(tool.run, query="from a thread")
        assert "echo:from a thread" in out
        assert "echo:back home" in await tool._arun(query="back home")  # same loop, async


async def test_agent_refuses_a_second_loop(server, loopback):
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        await agent.card()
        with pytest.raises(RuntimeError, match="bound to another event loop"):
            await asyncio.to_thread(asyncio.run, agent.send("hi"))
