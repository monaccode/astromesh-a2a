"""Helpers for local development and tests. UNSAFE: never use them in production.

The egress guard refuses private and loopback addresses, so a ``RemoteAgent`` cannot
reach ``127.0.0.1`` by default, and the library has no ``Policy`` flag that changes it.
``allow_loopback`` is the one explicit, per-agent way to talk to an agent on this
machine (an example, a test, the attack eval).
"""

from __future__ import annotations

from astromesh_a2a.client import RemoteAgent

__all__ = ["allow_loopback"]


def allow_loopback(agent: RemoteAgent) -> None:
    """Let THIS agent reach private/loopback addresses. Local development and tests only —
    never production.

    The agent's card and RPC HTTP clients become ``net.safe_client(...,
    allow_private=True)``: the same pinned transport (body caps, no redirects, one host
    per client), only the public-address rule is lifted. ``Policy.allowed`` and every
    other check still apply (list ``"127.0.0.1"``; plain ``http`` still needs
    ``allow_insecure_http=True``). Any client the agent already opened is replaced (and
    closed on ``aclose()``); call it before the first request.
    """
    if not isinstance(agent, RemoteAgent):
        raise TypeError("allow_loopback needs a RemoteAgent")
    agent._allow_private = True
    stale = [c for c in (agent._card_http, agent._rpc_http) if c is not None]
    agent._retired.extend({id(c): c for c in stale}.values())
    agent._card_http = agent._rpc_http = None
    agent._clients = {}
