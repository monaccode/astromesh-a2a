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
