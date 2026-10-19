"""Adapters against the in-process reference server (real ``RemoteAgent``)."""

import asyncio
import threading

import pytest

from astromesh_a2a import Policy, RemoteAgent
from astromesh_a2a.adapters import run_sync
from astromesh_a2a.client import LoopBindingError


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
    with pytest.raises(LoopBindingError, match="bound to another event loop"):
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
        with pytest.raises(LoopBindingError, match="bound to another event loop"):
            await asyncio.to_thread(asyncio.run, agent.send("hi"))


async def test_tool_not_stuck_on_a_canceled_task(server, loopback):
    pytest.importorskip("langchain_core")
    async with RemoteAgent(server.base, policy=_policy()) as agent:
        await agent.card()
        tool = agent.as_tool("langchain")
        pending = await tool.ainvoke({"query": "ask"})
        task_id = pending.split('task_id="', 1)[1].split('"', 1)[0]
        await agent.cancel(task_id)  # the remote side ends the task
        out = await tool.ainvoke({"query": "Madrid"})  # reply to a dead task: tool error
        assert out.startswith("Remote A2A agent error (TaskFailed)")
        assert "echo:Paris" in await tool.ainvoke({"query": "Paris"})  # a new task
        assert "echo:Rome" in await tool.ainvoke({"query": "Rome"})


async def test_blocked_agent_loop_is_a_clear_error(server, loopback, monkeypatch):
    from astromesh_a2a.adapters import _common

    monkeypatch.setattr(_common, "_DISPATCH_GRACE", 0.2)
    policy = Policy(allowed=("127.0.0.1",), allow_insecure_http=True, timeout=0.5)
    async with RemoteAgent(server.base, policy=policy) as agent:
        await agent.card()
        remote = _common.RemoteTool.for_agent(agent, None, None)
        box = {}
        finished = threading.Event()

        def worker():  # a framework running the tool in a worker thread
            try:
                remote.call("hi")
            except Exception as exc:  # noqa: BLE001 - inspected below
                box["error"] = exc
            finally:
                finished.set()

        threading.Thread(target=worker, daemon=True).start()
        # The loop thread blocks, as a sync `crew.kickoff()` inside `async def main` would.
        assert finished.wait(10)
        assert isinstance(box["error"], LoopBindingError)
        assert "appears blocked" in str(box["error"])
        await asyncio.sleep(0.05)  # the cancelled call must not run once the loop is free
        assert server.rpc_requests() == []


async def test_aclose_from_another_live_loop_is_refused(server, loopback):
    agent = RemoteAgent(server.base, policy=_policy())
    await agent.card()
    with pytest.raises(LoopBindingError):
        await asyncio.to_thread(asyncio.run, agent.aclose())
    await agent.aclose()


def test_aclose_after_bound_loop_closed_drops_clients(server, loopback):
    agent = RemoteAgent(server.base, policy=_policy())
    asyncio.run(agent.card())
    asyncio.run(agent.aclose())  # no error: connections died with their loop
    assert agent._closed
