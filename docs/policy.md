# Policy

`Policy` is the client's rulebook for remote agents: which ones you may reach, what you
accept from them, and how much they may cost. It is a frozen dataclass; one policy can be
shared by many `RemoteAgent`s, and each agent keeps its own budget meter.

```python
from astromesh_a2a import Budget, Policy

policy = Policy(
    allowed=("trips.example.com", "https://hotels.example.org/.well-known/agent-card.json"),
    accept_parts=frozenset({"text", "data"}),
    timeout=30.0,
    budget=Budget(max_calls=20, max_seconds=120),
)
```

The default, `Policy()`, allows nothing: you must name every remote. Defaults are chosen so
that forgetting a setting fails closed.

## Fields

| Field | Default | What it does | Why this default |
|---|---|---|---|
| `allowed` | `()` | Bare hosts or full card URLs. A host matches the URL's hostname exactly, case-insensitively (no subdomains, port ignored, `a.com.` is a different host); a URL entry must match the whole URL exactly. A URL with user info (`user:pw@host`) is refused (`PolicyRejected("url")`): credentials go in `Auth`. | An agent should reach only the remotes you chose. Suffix matching is how `evil-example.com` gets in. |
| `require_signature` | `False` | Refuse cards without a valid JWS signature from a key in `signing_keys` (`PolicyRejected("signature")`). | Few agents sign their cards today. Turn it on for remotes that do: then `RemoteCard.signature_verified` is `True`. |
| `signing_keys` | `{}` | Map `kid -> key` (PEM or secret `str`/`bytes`, or a `jwt.PyJWK`). Needs the `signing` extra. | Keys are pinned by `kid`. A `jku` URL in the (untrusted) card header is never fetched: that would let the card choose its own key. |
| `signing_algorithms` | `("ES256", "ES384", "RS256", "PS256", "EdDSA")` | Algorithms accepted for card signatures. | Asymmetric algorithms only; never `none`. |
| `accept_parts` | `{"text", "data"}` | Part kinds kept from answers. Others are dropped and traced as `rejection`; if nothing acceptable is left, `PolicyRejected("part_kind:<kind>")`. | Files are the easiest way to push large or executable content into your agent. Opt in with `{"text", "data", "file"}`. |
| `max_bytes` | `1_048_576` (1 MiB) | Cap on every response body and every part (`BlockedDestination` / `PolicyRejected("part_size")`). Bodies are cut while reading, not after. | An answer bigger than this is rarely an answer; memory stays bounded. |
| `max_stream_bytes` | `8_388_608` (8 MiB) | Cap on a whole SSE stream; each event is also capped at `max_bytes`. | Streams legitimately carry more than one answer, but not unbounded. |
| `timeout` | `60.0` s | Wall-clock cap on each call (`send`, `reply`, `cancel`, card fetch) and on a whole stream, on top of HTTP timeouts (`A2ATimeout`). | A remote that never answers must not hang your agent. |
| `max_events` | `1000` | Cap on events per stream (`A2ATimeout`). | A stream that reports `working` forever is cut quickly even before `timeout`. |
| `budget` | `None` | `Budget(max_calls=None, max_seconds=None)` per `RemoteAgent`. Each call is admitted before anything is sent; when exhausted, `PolicyRejected("budget")`. A seconds budget also shortens each call's deadline to what is left. | Off by default because the right numbers depend on your agent; set it for anything that runs unattended. |
| `card_ttl` | `300.0` s | How long the vetted card is cached; then it is fetched and vetted again (all checks). | A remote can change its card; a refresh that fails the checks leaves no stale card behind. |
| `allow_insecure_http` | `False` | Accept `http://` card and interface URLs. | Credentials and answers travel in clear over http. Local development only. |

## Budgets

```python
from astromesh_a2a import Budget, Policy

policy = Policy(allowed=("trips.example.com",), budget=Budget(max_calls=5, max_seconds=60))
```

`max_calls` is exact. Seconds are wall-clock per call; for a stream, from start to end,
including your own time between events. Concurrent calls on the same agent each see the
same remaining seconds, so under concurrency the seconds cap is soft: it can be overrun by
up to the calls in flight.

## What the policy does not control

These hold whatever the policy says, and the eval checks them with a lax policy too:

- **The egress guard.** Private, loopback, link-local and other non-public addresses are
  refused, each host is resolved once and the connection pinned to the checked IP, and
  redirects are never followed. `Policy` has no flag to reach private addresses (for
  local development and tests, see [Loopback](#loopback-local-development-and-tests-only)).
- **Card vetting.** Only an A2A 1.x JSON-RPC interface, on the card's own host or an allowed
  one.
- **Untrusted output** in framework tools, and **no auto-confirm**.

## Signed cards

```python
from pathlib import Path

from astromesh_a2a import Policy

policy = Policy(
    allowed=("trips.example.com",),
    require_signature=True,
    signing_keys={"trips-2026": Path("trips-public.pem").read_text()},
    signing_algorithms=("ES256",),
)
```

Install the extra: `pip install 'astromesh-a2a[signing]'`. Get the remote's public key and
`kid` out of band (not from the card).

## Credentials and the interface host

`Auth` headers are sent on the card fetch and on every RPC, and only there. The card URL
passed the policy. The interface URL the card names must pass it too, with one exception:
an interface on the card's own hostname is accepted even when only a URL entry allows the
card. Hosts match without the port, so **an interface on another port of the card's host
(`https://trips.example.com:8443/a2a` for a card at `https://trips.example.com/...`)
receives your credentials**. That is usually the same operator; if another service shares
the hostname on another port, do not give this agent credentials it should not see, or
put the agent on its own hostname.

## Loopback: local development and tests only

The egress guard refuses `127.0.0.1`, so a `RemoteAgent` cannot reach an agent on your own
machine. `astromesh_a2a.testing.allow_loopback(agent)` lifts only the public-address rule,
for that one agent (the size caps, no-redirect rule and every policy check stay). It is
**unsafe: local development and tests only, never production**. The examples, the
test-suite and the attack eval use it.

```python
from astromesh_a2a import Policy, RemoteAgent
from astromesh_a2a.testing import allow_loopback

agent = RemoteAgent(
    "http://127.0.0.1:8000",
    policy=Policy(allowed=("127.0.0.1",), allow_insecure_http=True),
)
allow_loopback(agent)  # before the first request
```
