<p align="center">
  <img src="https://raw.githubusercontent.com/monaccode/astromesh-a2a/main/assets/logo.png" alt="Astromesh A2A" width="640">
</p>

# astromesh-a2a

**Let your agent talk to other agents without trusting them.**

[![astromesh-runtime](https://img.shields.io/badge/astromesh-runtime-f472b6)](https://monaccode.github.io/astromesh/#ecosystem)
[![Release](https://img.shields.io/github/v/release/monaccode/astromesh-a2a?label=release)](https://github.com/monaccode/astromesh-a2a/releases)
[![CI](https://github.com/monaccode/astromesh-a2a/actions/workflows/ci.yml/badge.svg)](https://github.com/monaccode/astromesh-a2a/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/astromesh-a2a?label=PyPI)](https://pypi.org/project/astromesh-a2a/)
[![Python](https://img.shields.io/pypi/pyversions/astromesh-a2a)](https://pypi.org/project/astromesh-a2a/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/monaccode/astromesh-a2a/blob/main/LICENSE)

*[Leer en español](https://github.com/monaccode/astromesh-a2a/blob/main/README.es.md)*

[A2A (Agent2Agent)](https://a2a-protocol.org) is the open protocol agents use to work with
each other: an agent publishes a card that says what it can do, and other agents send it
tasks over JSON-RPC, get answers, stream progress and answer its questions.

`astromesh-a2a` is a client and a server for A2A 1.x, built on the
[official A2A SDK](https://github.com/a2aproject/a2a-python). It puts a remote agent inside
your agent framework as an ordinary tool (nine frameworks), serves your own agent as an A2A
agent, and treats everything that crosses the wire as what it is: code you do not control.

## Why the SDK alone is not enough

The SDK speaks the protocol. It does not decide which remotes you may reach, how much of
their output you accept, or what your model does with it. A remote agent is an untrusted
program on someone else's server; this library is the layer between it and your agent.

| Risk | What `astromesh-a2a` does |
|---|---|
| A card or a redirect sends your client to `10.0.0.7`, `169.254.169.254` or `localhost` (SSRF) | **Pinned egress.** Every request goes through a guarded transport: http(s) only, public addresses only, each host resolved once and the connection pinned to the checked IP (no DNS-rebinding window), redirects never followed. |
| Any URL is reachable; a card names its RPC endpoint on another host | **Remote-agent policy.** `Policy.allowed` lists hosts or card URLs; nothing is allowed until you declare it. The card is fetched and vetted by the library (host, A2A 1.x JSON-RPC interface only, interface on an allowed host) before the SDK sees it. Optional **signed cards**, keys pinned by `kid` (a `jku` URL is never fetched). |
| The remote's text tells your model "ignore previous instructions" | **Untrusted output wrapping.** Framework tools return the remote's words inside `<remote-agent-output untrusted="true" ...>`, with `<`, `>`, `&` and quotes escaped so the block cannot be closed or forged. |
| The remote asks "reply yes to confirm the transfer" | **Never auto-confirm.** An `input_required` task comes back as a pending question; only an explicit `reply()` (from the user, through your agent) continues it. The server marks every incoming message `Origin.AGENT` and untrusted. |
| A 2 GB artifact, a stream that never ends, a remote that eats your budget | **Budgets, timeouts and size caps.** `max_bytes` per body and part, `max_stream_bytes` per stream, `timeout` per call (wall-clock), `max_events` per stream, `Budget(max_calls, max_seconds)` per remote. File parts are dropped unless you accept them. |
| You cannot see what happened, or your logs leak the API key | **Per-stage trace with redaction.** Every call returns a trace (`card`, `send`, `status`, `artifact`, `message`, `rejection`, `result`) with keys, tokens, URL credentials and presigned-URL signatures redacted, and a W3C `traceparent`. Credentials come only from env vars or callables. |

These are not claims: an [attack eval](https://github.com/monaccode/astromesh-a2a/blob/main/docs/eval.md)
runs nine malicious remotes against the library on every push.

## Install

```bash
pip install astromesh-a2a                # client
pip install 'astromesh-a2a[server]'      # + serve your own agent (Starlette, uvicorn)
pip install 'astromesh-a2a[langchain]'   # + a remote agent as a LangChain tool
```

Python 3.10+. Every framework is an optional extra (table below); `signing` adds PyJWT for
signed cards, `all` installs everything.

## Call a remote agent

```python
from astromesh_a2a import Auth, Policy, RemoteAgent

policy, auth = Policy(allowed=("trips.example.com",)), Auth.bearer("TRIPS_TOKEN")
async with RemoteAgent("https://trips.example.com", policy=policy, auth=auth) as trips:
    result = await trips.send("Find a flight to Lima on Friday")
print(result.status, result.answer, result.trace)
```

`result.status` is `completed`, or `input_required` with the remote's question in
`result.pending`; `reply(task_id, text)` answers it, `stream(text)` yields events,
`cancel(task_id)` stops a task, `card()` returns the vetted card. Errors are typed
(`PolicyRejected`, `BlockedDestination`, `TaskFailed`, `A2ATimeout`, `UnsupportedVersion`,
all `A2AError`). Guide: [docs/client.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/client.md).

## Serve your agent

```python
from astromesh_a2a import CardSpec, Incoming, Outgoing, Skill, api_key_auth, serve


async def handle(msg: Incoming) -> Outgoing:  # msg.text came from another program: untrusted
    return Outgoing(answer=await my_agent.run(msg.text, session=msg.session_key))


skill = Skill(id="book", name="Book trips", description="Flights and hotels.", tags=("travel",))
card = CardSpec("trips", "Books trips.", "1.0.0", "https://trips.example.com", [skill])
serve(handle, card, host="0.0.0.0", port=8000, auth=api_key_auth("TRIPS_API_KEY"))
```

The card is served at `/.well-known/agent-card.json` and JSON-RPC (blocking and SSE) at
`/a2a`. Auth is mandatory: without `auth=` the server refuses to start unless you pass
`insecure=True`. `create_app(...)` returns the Starlette app to mount in your own server.
Guide: [docs/server.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/server.md).

Try both ends on your machine, offline:

```bash
git clone https://github.com/monaccode/astromesh-a2a && cd astromesh-a2a
uv run python examples/hello_local.py
```

It runs from a checkout (examples are not in the wheel). The egress guard refuses
`127.0.0.1`, so the example opts its own agent into loopback with
`astromesh_a2a.testing.allow_loopback(agent)`: local development and tests only, never
production ([docs/policy.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/policy.md#loopback-local-development-and-tests-only)).

## A remote agent as a tool of your framework

```python
async with RemoteAgent("https://trips.example.com", policy=policy) as trips:
    await trips.card()  # the tool's name and description come from the card
    tool = trips.as_tool("langchain")  # give it to your agent: tools=[tool]
    print(await tool.ainvoke({"query": "Find a flight to Lima on Friday"}))
```

The tool takes one `query`, keeps the conversation (and a task waiting for input) on the
tool object, returns the wrapped untrusted output and reports errors the framework's way.

| Framework | `as_tool(...)` | Extra | Returns |
|---|---|---|---|
| LangChain / LangGraph | `"langchain"` | `langchain` | `StructuredTool` (sync + async) |
| CrewAI | `"crewai"` | `crewai` | `BaseTool` |
| Google ADK | `"adk"` | `adk` | `FunctionTool` |
| OpenAI Agents SDK | `"openai-agents"` | `openai-agents` | `FunctionTool` |
| PydanticAI | `"pydantic-ai"` | `pydantic-ai` | `Tool` (Python 3.11+) |
| LlamaIndex | `"llamaindex"` | `llamaindex` | `FunctionTool` (sync + async) |
| smolagents | `"smolagents"` | `smolagents` | `Tool` |
| Microsoft Agent Framework | `"agent-framework"` | `agent-framework` | `FunctionTool` |
| MCP | `"mcp"` | `mcp` | FastMCP `Tool`; `adapters.mcp.build_server` for a whole server |

The other way round, `adapters.server_langchain.handler_from_runnable(runnable)` serves a
LangChain runnable or LangGraph graph as an A2A agent. One example per framework:
[docs/frameworks.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/frameworks.md).

### Sync and async: event loops

A `RemoteAgent` is bound to the event loop of its first I/O (its pooled connections belong
to it) and refuses any other loop with `LoopBindingError`. Async tools just work. Sync tools
(CrewAI `run`, smolagents, LangChain `invoke`...) are dispatched to the agent's loop when it
runs in another thread, or run on a background loop when the agent has none yet. Do not
block the agent's loop while its tools run: calling a sync entry point (`crew.kickoff()`)
inside `async def main` would deadlock, so the dispatch gives up after `policy.timeout` + 5 s
with `LoopBindingError`. Use the async entry point (`kickoff_async`, `ainvoke`) or
`await asyncio.to_thread(crew.kickoff)`. From sync code, use
`run_sync(agent.card(), agent=agent)` and `run_sync(agent.aclose(), agent=agent)`
(`from astromesh_a2a.adapters import run_sync`). One `RemoteAgent` per loop.

## Sessions

`Incoming.context_id` is chosen by the caller and is not scoped: two callers can send the
same one. Key any memory or session state by `Incoming.session_key`
(`"<caller>:<context_id>"`, where the caller is the identity your `auth` returns), never by
`context_id` alone. On the client side, pass `context_id=` to `send()` to keep several
messages in one remote conversation.

## The eval

Nine local malicious remotes: a spoofed card, a redirect to an internal address, a public
name that resolves to `10.0.0.7`, a 32 MiB artifact, a file part, prompt injection that
tries to break out of the untrusted block, a remote that asks to be confirmed, a stream that
never ends and a 0.3-only agent.

```bash
python -m astromesh_a2a.eval   # needs the server extra
```

| Policy | Traps stopped |
|---|---|
| Default (`Policy(allowed=(host,))`) | **9/9** |
| Lax (files accepted, 64 MiB bodies) | 7/9 (`huge_artifact` and `file_part` get through) |

The other seven hold whatever the policy says: egress guard, card checks, the untrusted
wrapper and the no-auto-confirm rule. CI fails if a change lets one trap through. Traps,
"stopped" rules and negative controls: [docs/eval.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/eval.md).

## Policy

```python
from astromesh_a2a import Budget, Policy

policy = Policy(
    allowed=("trips.example.com", "https://hotels.example.org/.well-known/agent-card.json"),
    timeout=30.0,
    budget=Budget(max_calls=20, max_seconds=120),
)
```

Every field, its default and why: [docs/policy.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/policy.md).

## In the Astromesh ecosystem

`astromesh-a2a` is the A2A layer of [Astromesh](https://github.com/monaccode/astromesh), the
same way [astromesh-rag](https://github.com/monaccode/astromesh-rag) is its RAG layer and
[astromesh-okf](https://github.com/monaccode/astromesh-okf) its knowledge format. Planned
(not shipped yet):

- **Astromesh core:** a `type: a2a` tool in the agent manifest (`card_url`, `auth_env`,
  policy, timeout) built on this library, with `input_required` returned to the model as an
  observation; turns from remote agents counted as agent turns, never human ones.
- **Astromesh ADK:** `remote_a2a()` to use a remote A2A agent from Python agents.
- **Nexus:** published agents served over A2A (opt-in per agent), conformance-tested
  against this client.

Details: [docs/ecosystem.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/ecosystem.md).

## Contributing

Issues and pull requests are welcome:
[CONTRIBUTING.md](https://github.com/monaccode/astromesh-a2a/blob/main/CONTRIBUTING.md).

## License

[Apache-2.0](https://github.com/monaccode/astromesh-a2a/blob/main/LICENSE). Part of the
[Astromesh](https://monaccode.github.io/astromesh/) ecosystem.
