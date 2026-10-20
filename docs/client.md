# Client: `RemoteAgent`

A `RemoteAgent` is one remote A2A agent, reached only as far as its `Policy` allows.

```python
from astromesh_a2a import Auth, Origin, Policy, RemoteAgent

agent = RemoteAgent(
    "https://trips.example.com",  # base URL, or the card's full URL
    policy=Policy(allowed=("trips.example.com",)),
    auth=Auth.bearer("TRIPS_TOKEN"),  # optional
    origin=Origin.AGENT,  # the default
)
```

- **URL.** A base URL (empty or `/` path) gets `/.well-known/agent-card.json` appended;
  anything else is used as the card URL. The constructor checks it against the policy:
  an unlisted host never sees a request.
- **Lifetime.** Use `async with RemoteAgent(...) as agent:` or call `await agent.aclose()`.
  The agent owns its HTTP clients and closes them. A closed agent refuses new calls.
- **Event loop.** The agent is bound to the loop of its first I/O; another loop gets
  `LoopBindingError`. Create one `RemoteAgent` per loop (see the README, "Sync and async").

## How a call runs

1. The card is fetched by the library (never by the SDK) through the egress guard, then
   vetted: signature if `require_signature`, an A2A **1.x JSON-RPC** interface (a 0.3-only
   remote raises `UnsupportedVersion`), and an interface URL on the card's own host or on
   an allowed one (`PolicyRejected("interface:host_not_allowed")` otherwise).
2. The SDK client is built over the library's pinned HTTP client, from a copy of the card
   that keeps only the vetted interface.
3. The budget admits the call before anything leaves. The whole call runs under
   `policy.timeout` (or what is left of a seconds budget, if less).
4. Parts the policy does not accept are dropped and traced; the rest becomes a `Result`.

The vetted card is cached for `policy.card_ttl` seconds, then fetched and vetted again.

## `send()`

```python
result = await agent.send("Find a flight to Lima on Friday")
result = await agent.send("And a hotel", context_id=result.context_id)  # same conversation
result = await agent.send("Book it", data={"passengers": 2})  # plus a JSON data part
```

It waits until the task finishes or asks for something. The `Result`:

| Field | |
|---|---|
| `status` | `"completed"`, `"input_required"` or `"auth_required"` |
| `answer` | the accepted text parts, joined |
| `data` | the accepted data parts (JSON objects; numbers come back as floats, like any protobuf `Struct`) |
| `task_id`, `context_id` | the remote's ids |
| `pending` | when the remote asks for something, its question (never answered for you) |
| `trace` | the redacted per-stage trace of this call |

A task that ends `failed`, `rejected` or `canceled` raises `TaskFailed(remote=True)`.

## Confirmations: `reply()`

A remote that asks for input, even "reply 'yes' to confirm", gets no answer from the
library. `send()` returns `input_required` and only an explicit `reply()` continues the
task. Decide who answers: the user, through your agent.

```python
result = await agent.send("Transfer 500 USD to Ana")
if result.status == "input_required":
    answer = input(f"The remote asks: {result.pending}\n> ")  # a human decides
    result = await agent.reply(result.task_id, answer)
```

The remote's question is remote text: show it, do not obey it.

## `stream()`

```python
async with agent.stream("Plan a 3-day trip to Cusco") as events:
    async for event in events:
        if event.kind == "artifact":
            print(event.payload["text"], end="")
        elif event.kind == "status":
            print(f"\n[{event.payload['state']}]")
    print(events.trace)
```

`stream()` is a plain method (no `await`) that returns an `EventStream`; nothing is sent
until the first event is read. Each `Event` has `kind` (`"status"`, `"artifact"`,
`"message"`) and a `payload` dict with `task_id`, `context_id`, `text`, `data` and
`untrusted=True`; status payloads add `state` and `final`, artifact payloads add
`artifact_id`, `name`, `append` and `last_chunk`.

The stream ends after the first final status (`completed`, `failed`, `canceled`,
`rejected`, `input_required`, `auth_required`) or a message. A failed task is an event,
not an exception. Past `policy.timeout` (whole stream) or `policy.max_events`, it raises
`A2ATimeout` and closes the remote stream. Leaving the `async with` (or `aclose()`) closes
it too; a bare `async for` + `break` leaves that to the garbage collector.

## `cancel()` and `card()`

```python
await agent.cancel(result.task_id)  # TaskFailed if the task already ended otherwise

card = await agent.card()
print(card.name, card.version, card.interface_url, [s.name for s in card.skills])
```

`card()` returns a frozen `RemoteCard`: `name`, `description`, `version`, `interface_url`,
`protocol_version`, `streaming`, `has_signatures`, `signature_verified`, `skills` and `raw`
(a copy of the vetted SDK card). Every text field was written by the remote.
`has_signatures` only says the card carries signatures; trust `signature_verified`, which
is `True` only when the policy required a signature and it verified.

## Credentials: `Auth`

```python
from astromesh_a2a import Auth

Auth.bearer("TRIPS_TOKEN")  # Authorization: Bearer $TRIPS_TOKEN
Auth.api_key("TRIPS_KEY", header="X-API-Key")  # X-API-Key: $TRIPS_KEY
Auth.from_callable(lambda: "Bearer " + "token-from-your-refresher")  # called per request
```

The secret is read at call time from the environment variable or the callable. It is never
stored on the object, and never appears in a trace, a `repr` or an error message. A missing
secret raises `PolicyRejected("auth")` before anything is sent. Credentials go to the RPC
interface the card names (on the card's host or another allowed host).

## Origin and tracing

Every message carries `origin` (`"agent"` by default, `Origin.HUMAN` only if a person typed
it) and a W3C `traceparent` in its metadata; `traceparent` is also sent as a header. The
trace is a list of stage dicts, each with `stage` and `ms`:

```python
for entry in result.trace:
    print(entry["stage"], entry["ms"], entry)
```

Stages: `card` (fetched or `cached`), `send`, `status`, `artifact`, `message`, `rejection`
(a part dropped by the policy, with its kind) and `result`. Keys that look like secrets,
URL user info, credential query parameters (presigned-URL signatures included) and
Bearer/Basic values are redacted.

## Errors

All inherit from `A2AError`.

| Error | Raised when |
|---|---|
| `PolicyRejected(reason)` | The policy refused: `host_not_allowed`, `scheme`, `interface:...`, `card_invalid`, `card_fetch`, `part_kind:<kind>` (nothing acceptable came back), `part_size`, `budget`, `auth`, `signature`, `url`, `content_type` |
| `BlockedDestination(reason)` | The egress guard refused: private address, redirect, body over the cap |
| `TaskFailed` | The remote task failed/was rejected/canceled (`remote=True`), or the call failed in a way that cannot be classified (`remote=False`, generic message, the task may be alive) |
| `A2ATimeout` | A call or stream exceeded `timeout` or `max_events` |
| `UnsupportedVersion` | The remote does not speak A2A 1.x over JSON-RPC |
| `ConfirmationRequired` | Reserved; 0.1.0 never raises it (confirmations come back as `input_required`) |

`LoopBindingError` (a `RuntimeError`, not an `A2AError`) means the agent was used from
another event loop.
