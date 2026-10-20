# In the Astromesh ecosystem

`astromesh-a2a` works on its own, with any of the nine frameworks. It is also the A2A layer
of [Astromesh](https://github.com/monaccode/astromesh), the way
[astromesh-rag](https://github.com/monaccode/astromesh-rag) is its RAG layer and
[astromesh-okf](https://github.com/monaccode/astromesh-okf) its knowledge format.

**Status: planned.** Nothing below has shipped yet; each item gets its own release in the
repo named. This page says what is coming so that the library's choices make sense.

## Why the runtime needs it

Today an Astromesh agent calls another agent in process (`type: agent`) or through the
ADK's own protocol. Two problems the library was designed around:

- **Remote runs count as human turns.** A remote agent could answer a confirmation gate
  that is meant for a person. With A2A every remote turn is an agent turn
  (`Origin.AGENT`), and a remote answer never resolves a confirmation.
- **No standard way in or out.** Agents built with other frameworks cannot call an
  Astromesh agent, and Astromesh agents cannot call theirs.

## Phases

| Phase | Where | What |
|---|---|---|
| 0 | core | **Turn origin** (`human` or `agent`) on the run API and WebSocket; remote callers send `agent`. Parent-child traces; sub-agent cost added to the parent's usage; `spec.skills` in the agent manifest. |
| 1 | Nexus | **Nexus as an A2A server**: a card per published agent (opt-in per agent, off by default), JSON-RPC send/stream/get/cancel, the tenant's API key or JWT as auth, runs marked as agent turns. Written in Go and conformance-tested against this library's client. |
| 2 | core, ADK, Forge | **Client in the runtime**: a `type: a2a` tool in the manifest (`card_url`, `auth_env`, policy, `timeout`) built on this library (an `a2a` extra of the core), with `input_required` returned to the model as an observation and the task id kept per session; `remote_a2a()` in the ADK; Forge and the JSON schema. |
| 3 | Nexus | Asynchronous tasks and push notifications, with the same safe egress and signatures. |
| 4 | Clarus, Nexus | A business agent for personal agents, public A2A with OAuth scopes. Waits for that spec. |

## What a `type: a2a` tool will look like

A sketch of phase 2, not a shipped format:

```yaml
tools:
  - name: trips
    type: a2a
    card_url: https://trips.example.com
    auth_env: TRIPS_TOKEN
    policy:
      allowed: [trips.example.com]
      timeout: 30
```

The runtime will build a `RemoteAgent` from it and give the model the same tool contract the
framework adapters use: one `query`, untrusted output, pending questions returned to the
model and never answered for the user.

## Using it today

Everything the runtime will do is available now from Python: `RemoteAgent` and
`as_tool(...)` on the client side, `serve`/`create_app` on the server side. See
[client.md](client.md), [server.md](server.md) and [frameworks.md](frameworks.md).
