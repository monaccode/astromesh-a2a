# Server: `create_app` and `serve`

Expose any async agent as an A2A agent. Needs the `server` extra:

```bash
pip install 'astromesh-a2a[server]'
```

## The handler

Your agent is one async function, `Incoming -> Outgoing`:

```python
from astromesh_a2a import Incoming, Outgoing


async def handle(msg: Incoming) -> Outgoing:
    if msg.is_reply:  # an answer to a question this task asked
        return Outgoing(answer=f"Noted: {msg.text}")
    if "transfer" in msg.text:
        return Outgoing(needs_confirmation="Transfer 500 USD to Ana? Reply yes or no.")
    return Outgoing(answer="Done.", data=[{"booking": "LIM-0042"}])
```

**`Incoming`**

| Field | |
|---|---|
| `text`, `data` | the message's text (joined) and data parts; file parts are dropped |
| `origin` | always `Origin.AGENT`, whatever the sender claims |
| `untrusted` | always `True`: another program wrote this |
| `claimed_origin` | what the sender wrote as its origin (e.g. `"human"`), for logs only |
| `caller` | the identity your `auth` returned |
| `context_id` | chosen by the caller, not scoped (see sessions) |
| `session_key` | `"<caller>:<context_id>"`: key memory by this |
| `task_id` | always set (the SDK creates one for a new task) |
| `is_reply`, `pending` | `True` and the question it waited on when the message continues a task |

**`Outgoing`**: `answer` (text) and `data` (JSON objects) complete the task. With
`needs_confirmation` the task goes to `input_required` with that text instead. The remote's
next message on that task reaches your handler again as an untrusted `Incoming` with
`is_reply=True`: a remote "yes" is not a human confirmation, and the library never marks
anything as confirmed. Decide what an agent's answer is allowed to unlock.

A handler that raises, or runs past `handler_timeout` (default 300 s), fails the task with
a generic message ("The agent could not process this request."); the details go only to
the local log.

## The card

```python
from astromesh_a2a import CardSpec, Skill

card = CardSpec(
    name="trips",
    description="Books flights and hotels.",
    version="1.0.0",
    url="https://trips.example.com",  # the PUBLIC base URL the app is reachable at
    skills=[
        Skill(
            id="book",
            name="Book trips",
            description="Flights and hotels in South America.",
            tags=("travel", "booking"),
            examples=("A flight to Lima on Friday",),
        )
    ],
)
```

The card is published at `/.well-known/agent-card.json` and announces one JSON-RPC 1.0
interface at `<url>/a2a`. Give every skill at least one tag: the A2A spec requires it, and
the SDK warns otherwise. `streaming=False` turns off SSE in the card.

## Auth (mandatory)

```python
from astromesh_a2a import api_key_auth, create_app

app = create_app(handle, card, auth=api_key_auth("TRIPS_API_KEY", header="X-API-Key"))
```

`api_key_auth(env, header)` compares the header with the env var in constant time, reads
the variable on every request (rotation works) and refuses everything when it is unset.
Any `async (Request) -> bool | str` works too: return a string to name the caller (tasks
and `session_key` are scoped per caller), `True` for one shared identity, a falsy value or
an exception to refuse with `401`.

```python
from starlette.requests import Request


async def by_token(request: Request) -> str | None:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return await lookup_tenant(token)  # your code: tenant id, or None
```

The card is public; everything else goes through `auth` before the SDK sees it. Without
`auth`, `create_app` and `serve` raise `ValueError` unless you pass `insecure=True` (local
experiments only: every caller then shares the same identity and can read or cancel anyone's
task by id).

## Running it

```python
from astromesh_a2a import serve

serve(handle, card, host="0.0.0.0", port=8000, auth=api_key_auth("TRIPS_API_KEY"))
```

`serve` runs uvicorn and blocks. `create_app` returns the Starlette app, to run under your
own server or mount in an existing one:

```python
from fastapi import FastAPI

api = FastAPI()
# card.url must include the mount path: CardSpec(..., url="https://api.example.com/agents/trips")
api.mount("/agents/trips", create_app(handle, card, auth=api_key_auth("TRIPS_API_KEY")))
```

A mounted sub-app's lifespan does not run under Starlette. The app's lifespan only closes
the SDK request handler on shutdown; a host app that needs that cleanup has to do it itself.

## Options

| Option | Default | |
|---|---|---|
| `auth` | required | see above |
| `insecure` | `False` | serve without auth (local only) |
| `max_body_bytes` | 1 MiB | a larger request body answers `413` (the SDK reads bodies unbounded); a malformed `Content-Length` answers `400` |
| `handler_timeout` | 300 s | `None` for no limit; past it the task fails |
| `task_store` | SDK `InMemoryTaskStore` | lost on restart and unbounded: pass a persistent or bounded `TaskStore` in production. It **must scope tasks by owner** (see below) |

Only `A2A-Version: 1.x` is served; a request without the header means 0.3 and is refused
with JSON-RPC `-32009`.

## Sessions

Tasks are scoped by owner, the identity your `auth` returned: one caller cannot get,
cancel or reply to another caller's task, even knowing its id. That scoping lives in the
task store. A custom `task_store` must keep it: resolve the owner from the
`ServerCallContext` the SDK passes to every `save`/`get`/`list`/`delete` and filter by it,
as the SDK's stores do (`owner_resolver`, default `resolve_user_scope`). With
`insecure=True` every caller is the same owner and shares every task.

`context_id` is chosen by the caller and is not scoped: two callers can send the same one.
Key memory and session state by `msg.session_key`, never by `context_id` alone.

## Serving a LangChain runnable

```python
from astromesh_a2a.adapters.server_langchain import handler_from_runnable

serve(handler_from_runnable(graph), card, auth=api_key_auth("TRIPS_API_KEY"))
```

The input follows the runnable's schema (the text, `{"messages": [...]}` for a graph, its
single required field, or `{"input": ...}`), `configurable.thread_id` is the
`session_key`, and the result's text becomes the answer. See `examples/serve_langchain.py`.

## Logs

The SDK logs request bodies at `DEBUG`. Keep the `a2a` logger at `INFO` or above in
production if bodies may hold personal data.
