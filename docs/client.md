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

The agent remembers each task's context (`context_id`) from the results and events it
has seen, and `reply()` sends it with the answer. When the task came from somewhere else
(another `RemoteAgent`, a process restart, a task id kept in your database), pass it:

```python
result = await agent.reply(task_id, answer, context_id=saved_context_id)
```

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

## Tasks: `submit()`, `get_task()`, `subscribe()`, `list_tasks()`

`send()` waits for the task. For long tasks, start one and come back to it (A2A 1.0
`returnImmediately`, `GetTask`, `SubscribeToTask`, `ListTasks`):

```python
task = await agent.submit("Compile the quarterly report")  # returns at once
print(task.status, task.task_id)  # "submitted" or "working"

# Poll it...
result = await agent.get_task(task.task_id)

# ...or follow its events (same Event objects and rules as stream()).
async with agent.subscribe(task.task_id) as events:
    async for event in events:
        print(event.kind, event.payload.get("state"), event.payload["text"])

# Your tasks on that remote, a page at a time.
page = await agent.list_tasks(status="working", page_size=20)
for t in page.tasks:
    print(t.task_id, t.status)
if page.next_page_token:
    page = await agent.list_tasks(status="working", page_size=20, page_token=page.next_page_token)
```

| Method | Returns | |
|---|---|---|
| `submit(text, *, context_id=None, data=None)` | `Result` | Same message as `send()`, without waiting: usually `submitted`/`working` with its `task_id`. |
| `get_task(task_id)` | `Result` | The task as it is now. |
| `subscribe(task_id)` | `EventStream` | The task so far (artifacts, then status), then its events until a final status. |
| `list_tasks(*, context_id=None, status=None, page_size=None, page_token=None)` | `TaskPage` | One page of the caller's tasks. |

These report a task as it is, so a task's `Result.status` can be any A2A state:
`submitted`, `working` (`answer`/`data`: the output so far), `completed`,
`input_required`/`auth_required` (`pending`: the question), and `failed`, `canceled`,
`rejected` (`answer`: the remote's status message). A task that ended badly is a status
here, not an exception (`send()` still raises `TaskFailed` for it).

`TaskPage` has `tasks` (a list of `Result`, each with an empty `trace`), `next_page_token`
(`None` on the last page), `total_size` (as reported by the remote) and `trace` (the
call's). `status` filters by one of the state names above; `page_size` is 1-100.

The same guards apply to every call: the budget admits it first, `policy.timeout` caps it
(for `subscribe()`, the whole stream, plus `policy.max_events`), parts go through
`accept_parts` and `max_bytes`, the trace is redacted, and remote errors become our
errors. An unknown task, or one owned by another caller, raises `TaskFailed(remote=True)`.
`subscribe()` to a task that already ended raises it too (use `get_task()`), and needs a
remote whose card declares streaming. In `list_tasks()`, a task whose parts were all of
a refused kind is listed with them dropped (traced) instead of failing the whole page;
the page is one response body, so `max_bytes` caps it: keep pages small.

A server built with `create_app` supports all four (see [server.md](server.md#tasks)).

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
secret raises `PolicyRejected("auth")` before anything is sent, card fetch included.

Credentials go only to allowed hosts: to the card fetch (the card URL is the one you
configured, and the policy allowed it; some servers, such as Astromesh Nexus, authenticate
the card too) and to the RPC interface the card names (on the card's host or another
allowed host). A card whose interface is on a host the policy does not allow is refused
before any RPC, so the credentials never leave for it. See
[policy.md](policy.md#credentials-and-the-interface-host) for the same-host, other-port
case.

## Origin and tracing

Every message carries `origin` (`"agent"` by default, `Origin.HUMAN` only if a person typed
it) and a W3C `traceparent` in its metadata; `traceparent` is also sent as a header. The
trace is a list of stage dicts, each with `stage` and `ms`:

```python
for entry in result.trace:
    print(entry["stage"], entry["ms"], entry)
```

Stages: `card` (fetched or `cached`), `send` (with the A2A `method`), `status`, `artifact`, `message`, `rejection`
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

Confirmations are not errors: they come back as `input_required` (see above).

Two local errors are `RuntimeError`s, not `A2AError`s, and are never translated:
`LoopBindingError` means the agent was used from another event loop, and
`AgentClosedError` that it was used after `aclose()`.
