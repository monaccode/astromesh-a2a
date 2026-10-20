# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-20

First release.

### Added

- `net`: pinned egress transport (`PinnedTransport`, `safe_client`). Only http/https and
  public hosts; each host is resolved once and the connection pinned to the checked IP
  (no DNS-rebinding window); no redirects; decoded response bodies capped; guard
  failures raise `BlockedDestination`.
- `Policy`: allow-list of hosts/card URLs (nothing allowed by default; URLs with user
  info refused), accepted part kinds, byte/time/event caps, budgets (`Budget`, with
  `BudgetMeter.admit()`/`remaining_seconds()`/`record_seconds()`), optional card
  signatures with keys pinned by `kid` (`signing_keys`, `signing_algorithms`; `signing`
  extra, PyJWT), `max_stream_bytes` (default 8 MiB) and `card_ttl` (default 300 s: the
  card is fetched and vetted again after it expires; a refresh that fails the checks
  leaves no stale card behind).
- Typed errors: `A2AError`, `PolicyRejected`, `BlockedDestination`, `TaskFailed`
  (`remote=True` only when the remote itself reported the task ended), `A2ATimeout`,
  `UnsupportedVersion`; local `RuntimeError`s `LoopBindingError` and `AgentClosedError`,
  never translated.
- `Trace`: per-stage trace with secret redaction (keys, URL userinfo, query parameters
  incl. presigned-URL signatures/credentials, Bearer/Basic values) and W3C `traceparent`.
- `RemoteAgent` on the official `a2a-sdk`: the card is fetched and vetted by the library
  (policy host, signature, A2A 1.x JSON-RPC interface only, interface host allowed)
  before any SDK client exists; `card()` returns a frozen `RemoteCard` (`name`,
  `description`, `version`, `interface_url`, `protocol_version`, `streaming`,
  `has_signatures`, `signature_verified`, `skills`, `.raw`). `send()`, `reply(task_id,
  text)`, `cancel(task_id)` and `stream()` (an `EventStream` of `Event(kind, payload)`
  with its own trace) each run under the policy timeout; streams are also capped by
  `max_events` and closed on `async with` exit or `aclose()`. Origin and `traceparent`
  go in the message metadata. Parts the policy does not accept are dropped and traced;
  `PolicyRejected` only when nothing acceptable came back. A confirmation is never
  answered automatically: `send()` returns `input_required` with the question in
  `Result.pending`, and only an explicit `reply()` continues the task. Every call is
  admitted against the budget before any request (card included) leaves. Unexpected
  SDK errors become a generic `TaskFailed` that never quotes the remote. An agent is
  bound to the event loop of its first I/O and refuses others.
- `Auth` (`bearer`, `api_key`, `from_callable`): secrets read at call time from env vars
  or callables, never stored, traced or shown in a repr. Sent on the card fetch and on
  every RPC, only to policy-allowed hosts.
- `astromesh_a2a.testing.allow_loopback(agent)`: the explicit, per-agent opt-in to reach
  loopback, for local development and tests only (never production); `Policy` has no
  flag for it.
- Server (`server` extra, imported lazily): `create_app(handler, card, auth=...)`
  (Starlette, mountable) and `serve(...)` (uvicorn) expose any async agent as an A2A
  agent: card at `/.well-known/agent-card.json` built from `CardSpec`/`Skill`, JSON-RPC
  1.0 (blocking and SSE) at `/a2a`. Auth is mandatory (`api_key_auth(env, header)`,
  constant-time and fail closed, or any `async (Request) -> bool | str`; `401` JSON on
  refusal); without it `create_app`/`serve` raise `ValueError` unless `insecure=True`.
  Bodies over `max_body_bytes` (default 1 MiB) answer `413`, a malformed
  `Content-Length` `400`. Only `A2A-Version: 1.x` is served. Tasks are scoped by caller
  identity; `task_store=` (default: the SDK's in-memory store) must keep that scoping.
  `handler_timeout` (default 300 s) and handler exceptions fail the task with a generic
  message.
- `Incoming`/`Outgoing`: every incoming message reaches the handler as `Origin.AGENT`,
  `untrusted=True`, whatever the remote claims (`claimed_origin` kept for logging only);
  file parts are dropped. `Outgoing.needs_confirmation` puts the task in
  `input_required`; the remote's reply comes back as untrusted input with `is_reply` and
  `pending`, and the library never marks anything as confirmed. `Incoming.session_key`
  (`"<caller>:<context_id>"`) is what to key memory by.
- `RemoteAgent.as_tool(framework)` and `astromesh_a2a.adapters`: a remote agent as a
  native tool of LangChain, CrewAI, Google ADK, OpenAI Agents SDK, PydanticAI,
  LlamaIndex, smolagents, Microsoft Agent Framework and MCP (FastMCP; `build_server`
  serves it as a whole MCP server). One `query` input; the tool keeps one conversation
  and replies to a task waiting for input; output wrapped by `wrap_untrusted()` with a
  note that only the user may answer a pending question; errors use each framework's
  convention with a safe message; names (from the card or `name=`) sanitized for every
  framework. Sync calls are dispatched to the agent's loop (`run_sync`) and queued per
  tool before dispatch. `MissingExtraError` names the extra to install.
- `adapters.server_langchain.handler_from_runnable(runnable)`: a LangChain runnable as a
  `serve()` handler.
- Attack eval (`astromesh_a2a.eval`, `python -m astromesh_a2a.eval`): nine local
  malicious remotes (`card_spoof`, `redirect_internal`, `dns_private`, `huge_artifact`,
  `file_part`, `injection_text`, `self_confirm`, `endless_stream`, `version_0_3`), each
  with a precise "stopped" rule; the default policy stops 9/9 and CI enforces it.
- Documentation (README in English and Spanish, guides under `docs/`), logo, runnable
  examples (`examples/hello_local.py` serves and calls an agent offline), CI (lint, core
  matrix 3.10-3.13, eval, adapters with every extra) and a release workflow (TestPyPI,
  smoke install from the wheel's metadata, PyPI and GitHub Release on a `v*` tag).

[Unreleased]: https://github.com/monaccode/astromesh-a2a/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/monaccode/astromesh-a2a/releases/tag/v0.1.0
