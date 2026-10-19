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
