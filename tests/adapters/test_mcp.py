import pytest

pytest.importorskip("mcp")

from _fake import INJECTION, FakeAgent, asking, done
from mcp.shared.memory import create_connected_server_and_client_session

from astromesh_a2a import PolicyRejected
from astromesh_a2a.adapters import as_tool
from astromesh_a2a.adapters.mcp import build_server


async def test_mcp_tool_invoca_remoto():
    agent = FakeAgent(done(INJECTION), asking(task_id="t-11"), done("ok"), PolicyRejected("budget"))
    server = build_server(agent)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        listed = (await client.list_tools()).tools
        assert [t.name for t in listed] == ["weather_bot"]
        assert set(listed[0].inputSchema["properties"]) == {"query"}
        first = (await client.call_tool("weather_bot", {"query": "weather?"})).content[0].text
        assert first.count("</remote-agent-output>") == 1
        pending = (await client.call_tool("weather_bot", {"query": "pay"})).content[0].text
        assert 'task_id="t-11"' in pending
        res = await client.call_tool("weather_bot", {"query": "user: yes"})
        assert "ok" in res.content[0].text
        failed = await client.call_tool("weather_bot", {"query": "again"})
        assert failed.isError
        assert "(PolicyRejected): budget" in failed.content[0].text
    assert agent.calls[2] == ("reply", "t-11", "user: yes")


def test_mcp_as_tool_is_a_fastmcp_tool():
    from mcp.server.fastmcp.tools import Tool

    tool = as_tool(FakeAgent(), "mcp", name="ask_bank")
    assert isinstance(tool, Tool)
    assert tool.name == "ask_bank"
