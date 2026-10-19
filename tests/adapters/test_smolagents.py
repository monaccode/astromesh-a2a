import pytest

pytest.importorskip("smolagents")

from _fake import FakeAgent, asking, done

from astromesh_a2a import TaskFailed
from astromesh_a2a.adapters import RemoteToolError, as_tool


def test_smolagents_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-9"), done("ok"))
    tool = as_tool(agent, "smolagents")
    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool.inputs) == {"query"}
    assert "sunny" in tool(query="weather?")
    assert 'task_id="t-9"' in tool(query="pay")
    assert "ok" in tool(query="user: yes")
    assert agent.calls[-1] == ("reply", "t-9", "user: yes")


def test_smolagents_error():
    tool = as_tool(FakeAgent(TaskFailed("remote task FAILED")), "smolagents")
    with pytest.raises(RemoteToolError, match=r"\(TaskFailed\): remote task FAILED"):
        tool(query="x")
