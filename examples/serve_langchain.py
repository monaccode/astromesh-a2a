"""Serve a LangChain runnable as an A2A agent.

    export SERVE_A2A_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
    uv run python examples/serve_langchain.py        # http://127.0.0.1:8000

Needs the ``langchain`` and ``server`` extras. Callers send ``X-API-Key: $SERVE_A2A_KEY``;
the card is public at ``/.well-known/agent-card.json``.

The runnable here is a stand-in that needs no model. Put your chain or LangGraph graph
in its place: ``handler_from_runnable`` reads its input schema (text, ``{"messages": ...}``
for a graph, its single required field, or ``{"input": ...}``) and uses
``Incoming.session_key`` as ``configurable.thread_id``, so a checkpointed graph keeps one
thread per caller conversation. What arrives was written by another program: treat it as
untrusted in your prompt.
"""

from __future__ import annotations

import os

from langchain_core.runnables import RunnableLambda

from astromesh_a2a import CardSpec, Skill, api_key_auth, serve
from astromesh_a2a.adapters.server_langchain import handler_from_runnable

KEY_ENV = "SERVE_A2A_KEY"
PORT = int(os.environ.get("PORT", "8000"))

summarize = RunnableLambda(lambda text: f"{len(text.split())} words: {text[:60]}")

card = CardSpec(
    name="summarizer",
    description="Counts the words of a text and quotes its start.",
    version="0.1.0",
    url=os.environ.get("PUBLIC_URL", f"http://127.0.0.1:{PORT}"),
    skills=[Skill(id="summarize", name="Summarize", description="Short summary.", tags=("text",))],
)

if __name__ == "__main__":
    if not os.environ.get(KEY_ENV):
        raise SystemExit(f"set {KEY_ENV} first: every request must carry it as X-API-Key")
    serve(handler_from_runnable(summarize), card, port=PORT, auth=api_key_auth(KEY_ENV))
