import json

import pytest

pytest.importorskip("agents")

from _fake import FakeAgent, asking, done

from astromesh_a2a import PolicyRejected
from astromesh_a2a.adapters import as_tool


async def _call(tool, **kw):
    from agents.tool_context import ToolContext

    raw = json.dumps(kw)
    ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id="c1", tool_arguments=raw)
    return await tool.on_invoke_tool(ctx, raw)


async def test_openai_agents_tool_invoca_remoto():
    agent = FakeAgent(done("sunny"), asking(task_id="t-5"), done("ok"))
    tool = as_tool(agent, "openai-agents")
    assert tool.name == "weather_bot"
    assert "Forecasts" in tool.description
    assert set(tool.params_json_schema["properties"]) == {"query"}
    assert "sunny" in await _call(tool, query="weather?")
    assert 'task_id="t-5"' in await _call(tool, query="pay")
    assert "ok" in await _call(tool, query="user: yes")
    assert agent.calls[-1] == ("reply", "t-5", "user: yes")


async def test_openai_agents_error_goes_to_model():
    tool = as_tool(FakeAgent(PolicyRejected("part_kind:file")), "openai_agents")
    assert await _call(tool, query="x") == (
        "Remote A2A agent error (PolicyRejected): part_kind:file"
    )
