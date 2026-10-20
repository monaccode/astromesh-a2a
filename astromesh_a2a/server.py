"""Expose any async agent as an A2A agent: ``create_app`` (Starlette) and ``serve`` (uvicorn).

Needs the ``server`` extra: ``pip install 'astromesh-a2a[server]'``.

What the app does around the SDK (ADR 0001 §2, §6):

- ``GET /.well-known/agent-card.json``: the card built from ``CardSpec`` (public).
- ``POST /a2a``: JSON-RPC 1.0 (blocking and SSE), served by the SDK's
  ``DefaultRequestHandler`` over an in-memory task store.
- **Auth is mandatory.** The ``auth`` callable runs on every request except the card,
  BEFORE the SDK sees it; a falsy result or an exception answers ``401`` JSON. Without
  ``auth``, ``create_app``/``serve`` raise ``ValueError`` unless ``insecure=True``.
  ``auth`` may return a ``str``: that is the caller's identity, and tasks are scoped per
  identity (``True`` means one shared identity, ``"authenticated"``).
- **Body cap:** a request body over ``max_body_bytes`` answers ``413`` (the SDK reads
  bodies unbounded); a malformed ``Content-Length`` answers ``400``.
- **Version:** handled by the SDK. Only ``A2A-Version: 1.x`` is served; a missing header
  means 0.3 and is refused, like any other version, with JSON-RPC ``-32009``
  (VersionNotSupported). The v0.3 compat layer stays off.
- **Input:** text and data parts become an ``Incoming`` with ``origin=Origin.AGENT`` and
  ``untrusted=True`` no matter what the remote claims; file parts are dropped (logged).
  ``is_reply``/``pending`` tell a reply to a waiting task from a new message;
  ``session_key`` (caller + context id) is what to key memory by.
- **Limits:** ``handler_timeout`` per handler call (default 300 s) → ``failed``.
- **Output:** ``Outgoing`` → artifact ``answer`` + ``completed``; ``needs_confirmation``
  → ``input_required`` with that text. A handler exception → ``failed`` with a generic
  message; details only go to the local log.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

try:
    from a2a.helpers import new_task, new_text_part
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
    from a2a.types import (
        APIKeySecurityScheme,
        Part,
        SecurityRequirement,
        SecurityScheme,
        StringList,
        TaskState,
    )
    from google.protobuf.json_format import MessageToDict
    from starlette.applications import Starlette
    from starlette.authentication import AuthCredentials, SimpleUser
    from starlette.requests import Request
except ImportError as exc:  # pragma: no cover - depends on the installed extras
    raise ImportError(
        "astromesh_a2a.server needs the 'server' extra: pip install 'astromesh-a2a[server]'"
    ) from exc

from astromesh_a2a.card import RPC_PATH, CardSpec
from astromesh_a2a.types import Incoming, Origin, Outgoing

__all__ = [
    "DEFAULT_HANDLER_TIMEOUT",
    "DEFAULT_MAX_BODY_BYTES",
    "Handler",
    "api_key_auth",
    "create_app",
    "serve",
]

log = logging.getLogger("astromesh_a2a.server")

Handler = Callable[[Incoming], Awaitable[Outgoing]]
AuthCallable = Callable[[Request], Awaitable["bool | str"]]

CARD_PATH = "/.well-known/agent-card.json"
DEFAULT_MAX_BODY_BYTES = 1_048_576
FAILED_TEXT = "The agent could not process this request."
_MAX_CLAIM = 64
_SHARED_IDENTITY = "authenticated"
_WAITING = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
DEFAULT_HANDLER_TIMEOUT = 300.0


def api_key_auth(env: str, header: str = "X-API-Key") -> AuthCallable:
    """Auth that accepts requests whose ``header`` equals the secret in env var ``env``.

    The variable is read on every request (rotation works); if it is unset or empty,
    every request is refused (fail closed). Compared in constant time.
    """
    if not env or not header:
        raise ValueError("api_key_auth needs an env var name and a header")

    async def check(request: Request) -> bool:
        expected = os.environ.get(env, "")
        if not expected:
            log.warning("api_key_auth: env var %s is not set; refusing every request", env)
            return False
        given = request.headers.get(header, "")
        return hmac.compare_digest(given.encode(), expected.encode())

    check.security_scheme = ("apiKey", header)  # type: ignore[attr-defined]
    return check


# ---- input / output ------------------------------------------------------------------


def _incoming(context: RequestContext) -> Incoming:
    message = context.message
    texts: list[str] = []
    data: list[dict[str, Any]] = []
    dropped = 0
    claimed: str | None = None
    if message is not None:
        for part in message.parts:
            kind = part.WhichOneof("content")
            if kind == "text":
                texts.append(part.text)
            elif kind == "data":
                value = MessageToDict(part.data)
                data.append(value if isinstance(value, dict) else {"value": value})
            elif kind is not None:
                dropped += 1
        origin = message.metadata.fields.get("origin")
        if origin is not None and origin.WhichOneof("kind") == "string_value":
            claimed = origin.string_value[:_MAX_CLAIM]
    if dropped:
        log.info("dropped %d non text/data part(s) on task %s", dropped, context.task_id)
    if claimed is not None:
        log.debug("task %s: remote claims origin %r (treated as agent)", context.task_id, claimed)
    user = context.call_context.user if context.call_context else None
    caller = (user.user_name if user is not None and user.is_authenticated else "") or ""
    context_id = context.context_id or ""
    task = context.current_task
    pending = None
    if task is not None and task.status.state in _WAITING:
        pending = "\n".join(
            p.text for p in task.status.message.parts if p.WhichOneof("content") == "text"
        )
    return Incoming(
        text="\n".join(texts),
        data=data,
        origin=Origin.AGENT,
        context_id=context_id,
        untrusted=True,
        task_id=context.task_id,
        is_reply=task is not None,
        pending=pending,
        session_key=f"{quote(caller, safe='')}:{context_id}",
        claimed_origin=claimed,
        caller=caller or None,
    )


def _outgoing(value: Any) -> Outgoing:
    if isinstance(value, str):
        return Outgoing(answer=value)
    if not isinstance(value, Outgoing):
        raise TypeError(f"handler must return Outgoing or str, not {type(value).__name__}")
    if not isinstance(value.answer, str):
        raise TypeError("Outgoing.answer must be a str")
    if value.needs_confirmation is not None and not isinstance(value.needs_confirmation, str):
        raise TypeError("Outgoing.needs_confirmation must be a str or None")
    for item in value.data:
        if not isinstance(item, dict):
            raise TypeError("Outgoing.data items must be dicts")
    return value


def _data_part(item: dict[str, Any]) -> Part:
    part = Part()
    part.data.struct_value.update(json.loads(json.dumps(item, allow_nan=False)))
    return part


class _HandlerExecutor(AgentExecutor):
    """Runs the user's handler for each message (a new task, or a reply to one)."""

    def __init__(self, handler: Handler, timeout: float | None):
        self.handler = handler
        self.timeout = timeout

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        incoming = _incoming(context)  # before any event: reads the task as it was
        if context.current_task is None:  # the SDK needs the Task before any status
            history = [context.message] if context.message is not None else []
            await event_queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=history,
                )
            )
        up = TaskUpdater(event_queue, context.task_id, context.context_id)
        await up.start_work()
        try:
            out = _outgoing(await asyncio.wait_for(self.handler(incoming), self.timeout))
            parts = [new_text_part(out.answer)] if out.answer else []
            parts += [_data_part(item) for item in out.data]
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.error("handler timed out after %ss on task %s", self.timeout, context.task_id)
            await up.failed(up.new_agent_message([new_text_part(FAILED_TEXT)]))
            return
        except Exception:
            log.exception("handler failed on task %s", context.task_id)
            await up.failed(up.new_agent_message([new_text_part(FAILED_TEXT)]))
            return
        if out.needs_confirmation is not None:
            question = out.needs_confirmation or "Confirmation required."
            await up.requires_input(up.new_agent_message([new_text_part(question)]))
            return
        if parts:
            await up.add_artifact(parts, name="answer")
        await up.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


# ---- auth + body cap (pure ASGI, in front of the SDK) ------------------------------------


async def _send_json(send: Any, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class _Guard:
    """Auth then body cap, before the SDK. The card is public; everything else is not."""

    def __init__(self, app: Any, auth: AuthCallable | None, max_body_bytes: int):
        self.app = app
        self.auth = auth
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        public = scope["path"].endswith(CARD_PATH) and scope["method"] in ("GET", "HEAD")
        if not public and self.auth is not None:
            identity = await self._authenticate(scope)
            if identity is None:
                await _send_json(send, 401, {"error": "unauthorized"})
                return
            scope["user"] = SimpleUser(identity)
            scope["auth"] = AuthCredentials(["authenticated"])
        if public:
            await self.app(scope, receive, send)
            return
        try:
            body = await self._read_body(scope, receive)
        except ValueError:
            await _send_json(send, 400, {"error": "invalid content-length"})
            return
        if body is None:
            await _send_json(send, 413, {"error": "request body too large"})
            return
        await self.app(scope, _replay(body, receive), send)

    async def _authenticate(self, scope: dict) -> str | None:
        assert self.auth is not None
        try:
            verdict = await self.auth(Request(scope))  # headers only: no body access
        except Exception:
            log.exception("auth callable raised; request refused")
            return None
        if verdict is True:
            return _SHARED_IDENTITY
        if isinstance(verdict, str) and verdict:
            return verdict
        return None

    async def _read_body(self, scope: dict, receive: Any) -> bytes | None:
        """The whole body, or None past ``max_body_bytes`` (checked as it streams in).

        ``ValueError`` for a malformed ``Content-Length`` (answered ``400``).
        """
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                if not value.isdigit():  # also refuses "-1", "+5", " 5", "1_0"
                    raise ValueError("invalid content-length")
                if int(value) > self.max_body_bytes:
                    return None
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)


def _replay(body: bytes, receive: Any) -> Any:
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()  # after the body: disconnect notifications (SSE)

    return replay


def _security(card: Any, auth: Any) -> None:
    scheme = getattr(auth, "security_scheme", None)
    if not scheme or scheme[0] != "apiKey":
        return
    card.security_schemes["apiKey"].CopyFrom(
        SecurityScheme(
            api_key_security_scheme=APIKeySecurityScheme(location="header", name=scheme[1])
        )
    )
    requirement = SecurityRequirement()
    requirement.schemes["apiKey"].CopyFrom(StringList())
    card.security_requirements.append(requirement)


# ---- public API ---------------------------------------------------------------------------


def create_app(
    handler: Handler,
    card: CardSpec,
    *,
    auth: AuthCallable | None = None,
    insecure: bool = False,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    handler_timeout: float | None = DEFAULT_HANDLER_TIMEOUT,
    task_store: TaskStore | None = None,
) -> Starlette:
    """A Starlette app serving ``handler`` as an A2A agent described by ``card``.

    ``auth`` is required (``api_key_auth(...)`` or any ``async (Request) -> bool | str``);
    ``insecure=True`` serves without it (local experiments only). With ``insecure`` every
    caller has the same (empty) identity, so all callers SHARE tasks: anyone can read or
    cancel anyone's task by id.

    ``handler_timeout`` (seconds, ``None`` = no limit) caps each handler call; past it the
    task fails with the generic message. ``task_store`` defaults to the SDK's
    ``InMemoryTaskStore``: lost on restart and unbounded (it keeps every task), fine for
    development; pass a persistent/bounded ``TaskStore`` in production. Tasks are scoped
    by owner (the identity ``auth`` returned): one caller cannot get, cancel or reply to
    another's task. A custom ``task_store`` MUST keep that scoping: resolve the owner
    from the ``ServerCallContext`` the SDK passes to every ``save``/``get``/``list``/
    ``delete`` and filter by it, as the SDK stores do (``owner_resolver``, default
    ``resolve_user_scope``).

    Mountable under a sub-path: then ``card.url`` must include that path. The app's
    lifespan closes the SDK request handler on shutdown; a mounted sub-app's lifespan does
    not run under Starlette, so the host app should call that cleanup itself if needed.
    """
    if not callable(handler):
        raise TypeError("handler must be an async callable")
    if not isinstance(card, CardSpec):
        raise TypeError("card must be a CardSpec")
    if auth is None and not insecure:
        raise ValueError("an A2A server needs auth= (e.g. api_key_auth(...)) or insecure=True")
    if auth is not None and not callable(auth):
        raise TypeError("auth must be an async callable")
    if not isinstance(max_body_bytes, int) or max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be a positive int")
    if handler_timeout is not None and not handler_timeout > 0:
        raise ValueError("handler_timeout must be > 0 or None")
    if auth is None:
        log.warning("A2A server %r runs WITHOUT authentication (insecure=True)", card.name)
    agent_card = card.to_proto()
    if auth is not None:
        _security(agent_card, auth)
    request_handler = DefaultRequestHandler(
        agent_executor=_HandlerExecutor(handler, handler_timeout),
        task_store=task_store if task_store is not None else InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = [
        *create_agent_card_routes(agent_card, card_url=CARD_PATH),
        *create_jsonrpc_routes(request_handler, rpc_url=RPC_PATH),
    ]

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await request_handler.aclose()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(_Guard, auth=auth, max_body_bytes=max_body_bytes)
    return app


def serve(
    handler: Handler,
    card: CardSpec,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    auth: AuthCallable | None = None,
    insecure: bool = False,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    handler_timeout: float | None = DEFAULT_HANDLER_TIMEOUT,
    task_store: TaskStore | None = None,
) -> None:
    """Run ``create_app(...)`` under uvicorn (blocks). Refuses to start without ``auth``
    unless ``insecure=True``. Same options as ``create_app``."""
    app = create_app(
        handler,
        card,
        auth=auth,
        insecure=insecure,
        max_body_bytes=max_body_bytes,
        handler_timeout=handler_timeout,
        task_store=task_store,
    )
    try:
        import uvicorn
    except ImportError:
        raise ImportError("serve() needs uvicorn: pip install 'astromesh-a2a[server]'") from None
    uvicorn.run(app, host=host, port=port)
