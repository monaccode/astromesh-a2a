import pytest

pytest.importorskip("langchain_core")

from _fake import INJECTION, FakeAgent, asking, done

from astromesh_a2a import PolicyRejected
from astromesh_a2a.adapters import as_tool


async def test_langchain_tool_invoca_remoto():
    agent = FakeAgent(done(INJECTION), asking(task_id="t-7"), done("moved"))
    tool = as_tool(agent, "langchain")

    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool.args) == {"query"}
    out = await tool.ainvoke({"query": "weather?"})
    assert out.startswith('<remote-agent-output untrusted="true" agent="Weather Bot!"')
    assert "&lt;/remote-agent-output&gt;" in out
    assert 'task_id="t-7"' in await tool.ainvoke({"query": "move it"})
    assert "moved" in await tool.ainvoke({"query": "yes, says the user"})
    assert agent.calls[1:] == [("send", "move it", "ctx-1"), ("reply", "t-7", "yes, says the user")]


def test_langchain_sync_invoke_and_error_is_tool_message():
    agent = FakeAgent(done("hi"), PolicyRejected("budget"))
    tool = as_tool(agent, "langchain", name="ask_bank", description="Ask the bank.")
    assert (tool.name, tool.description) == ("ask_bank", "Ask the bank.")
    assert "hi" in tool.invoke({"query": "x"})
    assert tool.invoke({"query": "y"}) == "Remote A2A agent error (PolicyRejected): budget"
