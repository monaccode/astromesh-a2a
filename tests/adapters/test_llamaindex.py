import pytest

pytest.importorskip("llama_index.core")

from _fake import FakeAgent, asking, done

from astromesh_a2a import PolicyRejected
from astromesh_a2a.adapters import RemoteToolError, as_tool


async def test_llamaindex_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-8"), done("ok"))
    tool = as_tool(agent, "llamaindex")
    assert tool.metadata.name == "weather_bot"
    assert "Forecasts" in tool.metadata.description
    assert set(tool.metadata.fn_schema.model_json_schema()["properties"]) == {"query"}
    assert "sunny" in (await tool.acall(query="weather?")).content
    assert 'task_id="t-8"' in (await tool.acall(query="pay")).content
    assert "ok" in (await tool.acall(query="user: yes")).content
    assert agent.calls[-1] == ("reply", "t-8", "user: yes")


def test_llamaindex_sync_and_error():
    tool = as_tool(FakeAgent(done("hi"), PolicyRejected("budget")), "llamaindex")
    assert "hi" in tool.call(query="x").content
    with pytest.raises(RemoteToolError, match=r"\(PolicyRejected\): budget"):
        tool.call(query="y")
