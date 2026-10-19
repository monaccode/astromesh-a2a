# Changelog

## [Unreleased]

### Added

- `net`: pinned egress transport (`PinnedTransport`, `safe_client`). Only http/https and
  public hosts; each host is resolved once and the connection pinned to the checked IP
  (no DNS-rebinding window); no redirects; decoded response bodies capped; guard
  failures raise `BlockedDestination`.
- `Policy`: allow-list of hosts/card URLs (nothing allowed by default), accepted part
  kinds, byte/time/event caps, budgets (`Budget`, `BudgetMeter`), optional card
  signatures with keys pinned by `kid` (`signing_keys`, `signing_algorithms`).
- Typed errors: `A2AError`, `PolicyRejected`, `BlockedDestination`, `TaskFailed`,
  `A2ATimeout`, `UnsupportedVersion`, `ConfirmationRequired`.
- `Trace`: per-stage trace with secret redaction (keys, URL userinfo, query parameters
  incl. presigned-URL signatures/credentials, Bearer/Basic values) and W3C `traceparent`.
- `RemoteAgent`: card discovery and blocking `send()` on the official `a2a-sdk`. The card
  is fetched and vetted by us (policy host, signature, A2A 1.x JSON-RPC interface only,
  interface host allowed) before any SDK client exists; whole call under the policy
  timeout; origin and `traceparent` in the message metadata; parts the policy does not
  accept are dropped and traced. `Result`, `Origin` and `Auth` (env vars or callables).
- `signing` extra (`PyJWT`) for card signature verification.
- `RemoteAgent.stream()`: SSE events as `Event(kind, payload)` (`status`/`artifact`/
  `message`, plain dicts marked `untrusted`), in order, through an `EventStream` with its
  own trace; capped by `policy.timeout` (whole stream) and `policy.max_events`
  (`A2ATimeout`), stops after the first final status, closes the remote stream on
  `async with` exit or `aclose()`. Parts the policy does not accept are dropped and
  traced per event: status events are always emitted, artifact/message events with
  nothing accepted are dropped, and `PolicyRejected` only if the task completes having
  sent nothing acceptable. A closed `RemoteAgent` refuses streams created before closing.
- `RemoteAgent.reply(task_id, text)` continues a task that asked for input, and
  `RemoteAgent.cancel(task_id)`. A confirmation is never answered automatically: `send()`
  returns `input_required` and `stream()` emits it and ends.
- Budgets enforced per `RemoteAgent`: every `send`/`reply`/`stream`/`cancel` is admitted
  before any request (card included) leaves; exhausted → `PolicyRejected("budget")`. A
  seconds budget also caps each call's deadline. `BudgetMeter.admit()`,
  `remaining_seconds()`, `record_seconds()`.
- `Policy.max_stream_bytes` (default 8 MiB): an SSE response is capped in total at this
  and per event at `max_bytes` (in `net.PinnedTransport`); other bodies keep `max_bytes`.
- `Policy.card_ttl` (default 300 s): the card is fetched and vetted again after it
  expires; a refresh that fails the checks leaves no stale card behind.
- Server: `create_app(handler, card, auth=...)` (Starlette, mountable) and `serve(...)`
  (uvicorn; `server` extra, imported lazily) expose any async agent as an A2A agent on the
  official SDK: card at `/.well-known/agent-card.json` built from `CardSpec`/`Skill`,
  JSON-RPC 1.0 (blocking and SSE) at `/a2a`. Auth is mandatory: the `auth` callable
  (`api_key_auth(env, header)`, constant-time, fail closed when the env var is unset, or any
  `async (Request) -> bool | str`) runs before the SDK and refuses with `401` JSON; without
  it `create_app`/`serve` raise `ValueError` unless `insecure=True`. Request bodies over
  `max_body_bytes` (default 1 MiB) answer `413`. Only `A2A-Version: 1.x` is served (missing
  header = 0.3 → `-32009`).
- `Incoming`/`Outgoing`: every incoming message reaches the handler as `Origin.AGENT`,
  `untrusted=True`, whatever the remote claims (`claimed_origin` kept for logging only);
  file parts are dropped. `Outgoing.needs_confirmation` puts the task in `input_required`;
  the remote's reply comes back as untrusted input and the library never marks anything
  as confirmed. `Incoming.is_reply`/`pending` tell a reply to a waiting task (and the
  question it waited on) from a new message; `Incoming.session_key`
  (`"<caller>:<context_id>"`) is what to key memory by, since `context_id` is chosen by the
  caller. A handler exception, or a handler running past `handler_timeout` (default
  300 s), fails the task with a generic message; details stay in the local log.
- `create_app`/`serve` take `task_store=` (default: the SDK's unbounded in-memory store);
  the app's lifespan closes the SDK request handler on shutdown.
- `RemoteAgent.as_tool(framework, *, name=None, description=None)` and
  `astromesh_a2a.adapters`: a remote agent as a native tool of LangChain
  (`StructuredTool`), CrewAI (`BaseTool`), Google ADK (`FunctionTool`), OpenAI Agents SDK
  (`function_tool`) and PydanticAI (`Tool`); frameworks are imported on first use and a
  missing one raises `MissingExtraError` naming the extra. One input, `query`; the tool
  object keeps the conversation and replies to a task waiting for input (one tool per
  conversation). The output is `wrap_untrusted()`: the remote's words inside
  `<remote-agent-output untrusted="true" ...>` with `<`/`>`/`&` escaped, and for a
  pending task its `task_id` plus a note that only the user may answer it. Errors use
  each framework's tool-error convention with a safe message. Name and description
  default to the cached card (the remote's description quoted as untrusted). Sync calls
  run on one background event loop (`run_sync`); a sync call inside a running loop is
  refused. CI gains an `adapters` job with every extra installed.

### Changed

- **Breaking:** `RemoteAgent.card()` returns a frozen `RemoteCard` (`name`, `description`,
  `version`, `interface_url`, `protocol_version`, `streaming`, `has_signatures`,
  `signature_verified`, `skills` as `Skill`s) instead of the SDK protobuf `AgentCard`;
  `signature_verified` is `True` only when the policy required a signature and it
  verified. `RemoteCard.raw` keeps a copy of the vetted protobuf.
