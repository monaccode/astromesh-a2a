import pytest

pytest.importorskip("crewai")

from _fake import FakeAgent, asking, done

from astromesh_a2a import A2ATimeout
from astromesh_a2a.adapters import RemoteToolError, as_tool


def test_crewai_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-3"), done("booked"))
    tool = as_tool(agent, "crewai")
    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool.args_schema.model_json_schema()["properties"]) == {"query"}
    assert "sunny" in tool.run(query="weather?")  # sync: through the background loop
    assert 'task_id="t-3"' in tool.run(query="book")
    assert "booked" in tool.run(query="the user said yes")
    assert agent.calls[-1] == ("reply", "t-3", "the user said yes")


async def test_crewai_async_and_error():
    agent = FakeAgent(done("hi"), A2ATimeout("remote call exceeded 60s"))
    tool = as_tool(agent, "crewai")
    assert "hi" in await tool._arun(query="x")
    with pytest.raises(RemoteToolError, match=r"\(A2ATimeout\): remote call exceeded 60s"):
        await tool._arun(query="y")
