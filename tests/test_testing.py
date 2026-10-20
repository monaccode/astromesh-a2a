"""astromesh_a2a.testing.allow_loopback: one agent, explicitly, reaches loopback."""

from __future__ import annotations

import pytest

from astromesh_a2a import BlockedDestination, Policy, RemoteAgent
from astromesh_a2a.testing import allow_loopback


def _policy() -> Policy:
    return Policy(allowed=("127.0.0.1",), allow_insecure_http=True, timeout=10.0)


async def test_allow_loopback_lifts_only_this_agent(server):
    opted = RemoteAgent(server.base, policy=_policy())
    allow_loopback(opted)
    plain = RemoteAgent(server.base, policy=_policy())
    async with opted, plain:
        assert (await opted.send("hi")).answer == "echo:hi"
        with pytest.raises(BlockedDestination):
            await plain.send("hi")


async def test_allow_loopback_replaces_open_clients(server):
    agent = RemoteAgent(server.base, policy=_policy())
    async with agent:
        with pytest.raises(BlockedDestination):
            await agent.card()  # opened a normal (guarded) client
        stale = agent._card_http
        assert stale is not None
        allow_loopback(agent)
        assert agent._card_http is None
        assert (await agent.send("hi")).answer == "echo:hi"
    assert stale.is_closed  # the replaced client is still closed by aclose()


def test_allow_loopback_needs_a_remote_agent():
    with pytest.raises(TypeError):
        allow_loopback(object())  # type: ignore[arg-type]
