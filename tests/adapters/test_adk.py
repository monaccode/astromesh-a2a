import pytest

pytest.importorskip("google.adk")

from _fake import FakeAgent, asking, done

from astromesh_a2a import TaskFailed
from astromesh_a2a.adapters import as_tool


async def _call(tool, **kw):
    return await tool.run_async(args=kw, tool_context=None)


async def test_adk_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-4"), done("ok"))
    tool = as_tool(agent, "adk")
    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool._get_declaration().parameters.properties) == {"query"}
    assert "sunny" in await _call(tool, query="weather?")
    assert 'task_id="t-4"' in await _call(tool, query="pay")
    assert "ok" in await _call(tool, query="user: yes")
    assert agent.calls[-1] == ("reply", "t-4", "user: yes")


async def test_adk_error_is_error_dict():
    tool = as_tool(FakeAgent(TaskFailed("remote task FAILED")), "adk", name="bank")
    assert tool.name == "bank"
    assert await _call(tool, query="x") == {
        "error": "Remote A2A agent error (TaskFailed): remote task FAILED"
    }
