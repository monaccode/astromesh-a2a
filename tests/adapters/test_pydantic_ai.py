import pytest

pytest.importorskip("pydantic_ai")

from _fake import FakeAgent, asking, done

from astromesh_a2a import A2ATimeout, PolicyRejected
from astromesh_a2a.adapters import as_tool


async def test_pydantic_ai_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-6"), done("ok"))
    tool = as_tool(agent, "pydantic-ai")
    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool.function_schema.json_schema["properties"]) == {"query"}
    assert "sunny" in await tool.function(query="weather?")
    assert 'task_id="t-6"' in await tool.function(query="pay")
    assert "ok" in await tool.function(query="user: yes")
    assert agent.calls[-1] == ("reply", "t-6", "user: yes")


async def test_pydantic_ai_retries_only_timeouts():
    from pydantic_ai import ModelRetry

    tool = as_tool(
        FakeAgent(PolicyRejected("budget"), A2ATimeout("remote call exceeded 60s")), "pydantic-ai"
    )
    assert await tool.function(query="x") == "Remote A2A agent error (PolicyRejected): budget"
    with pytest.raises(ModelRetry, match=r"\(A2ATimeout\): remote call exceeded 60s"):
        await tool.function(query="y")
