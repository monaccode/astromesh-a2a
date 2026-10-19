import pytest

pytest.importorskip("agent_framework")

from _fake import FakeAgent, asking, done

from astromesh_a2a import PolicyRejected
from astromesh_a2a.adapters import RemoteToolError, as_tool


async def _call(tool, **kw):
    return (await tool.invoke(arguments=None, **kw))[0].text


async def test_agent_framework_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-10"), done("ok"))
    tool = as_tool(agent, "agent-framework")
    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool.parameters()["properties"]) == {"query"}
    assert "sunny" in await _call(tool, query="weather?")
    assert 'task_id="t-10"' in await _call(tool, query="pay")
    assert "ok" in await _call(tool, query="user: yes")
    assert agent.calls[-1] == ("reply", "t-10", "user: yes")


async def test_agent_framework_error():
    tool = as_tool(FakeAgent(PolicyRejected("budget")), "agent_framework")
    with pytest.raises(RemoteToolError, match=r"\(PolicyRejected\): budget"):
        await _call(tool, query="x")
