"""``RemoteAgent``: talk to a remote A2A agent through the egress guard and the Policy.

Flow of a call (ADR 0001 §1, §5):

1. ``Policy.check_card_url`` runs in the constructor: an unlisted host never sees a request.
2. The card is fetched by US through a pinned ``net.safe_client`` (never by the SDK), parsed
   with the SDK's ``parse_agent_card`` and vetted: signature (if required), protocol
   version (only a 1.x JSON-RPC interface; the SDK would silently fall back to 0.3) and the
   interface URL (same host as the card, or itself allowed by the policy).
3. An SDK ``Client`` is built over OUR httpx client from a copy of the card that keeps
   only the vetted interface, so the SDK cannot pick another one.
4. Every SDK call runs under ``policy.timeout`` wall-clock (or what is left of the seconds
   budget, if less), on top of httpx timeouts. A stream is also capped at
   ``policy.max_events`` events; either cap raises ``A2ATimeout``.
5. Budgets: one ``BudgetMeter`` per ``RemoteAgent``. Each ``send``/``reply``/``stream``/
   ``cancel`` is admitted BEFORE any request (card included) leaves; an exhausted budget
   raises ``PolicyRejected("budget")`` and nothing is sent. The time each call took is
   recorded afterwards.
6. The vetted card is cached ``policy.card_ttl`` seconds; then it is fetched and vetted
   again (all checks), and the SDK clients are rebuilt over the new card.

Confirmations are never answered here: ``send()`` returns ``input_required`` and
``stream()`` emits that status and ends. Only an explicit ``reply()`` continues the task.

Credentials: ``auth`` headers go on the card fetch (the card URL is the one the caller
configured, and the policy allowed it) and on every RPC (the interface URL passed the
policy, or is on the card's own host). They never go anywhere else, and never into a
trace, repr or error.

HTTP clients: ``net.safe_client`` pins ONE host per client. The card client is reused for
the RPC when the interface is on the same scheme/host/port; otherwise a second safe client
is opened for the interface host. ``RemoteAgent`` owns both and closes them in
``aclose()``. It never calls the SDK ``Client.close()``: that would close our httpx client.

Origin and ``traceparent`` go in the MESSAGE metadata (persisted in task history; the
server reads them at ``RequestContext.message.metadata``, not ``RequestContext.metadata``,
which is the request-level map). ``traceparent`` is also sent as the W3C header.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sys
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client import A2AClientTimeoutError, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.types import (
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskState,
    TaskStatus,
)
from a2a.utils import errors as sdk_errors
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict

from astromesh_a2a.card import RemoteCard
from astromesh_a2a.errors import (
    A2AError,
    A2ATimeout,
    BlockedDestination,
    PolicyRejected,
    TaskFailed,
    UnsupportedVersion,
)
from astromesh_a2a.policy import BudgetMeter, Policy
from astromesh_a2a.trace import Trace
from astromesh_a2a.types import Auth, Event, Origin, Result

WELL_KNOWN_CARD = "/.well-known/agent-card.json"
A2A_VERSION = "1.0"
_MAX_REMOTE_TEXT = 200
_MAX_VERSIONS_SHOWN = 5
_MAX_CONTEXTS = 256  # task_id -> context_id remembered for reply()
_FINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    }
)
_T = TypeVar("_T")


class AgentClosedError(RuntimeError):
    """The ``RemoteAgent`` was closed (``aclose()``) and then used. Local; never
    translated."""


class LoopBindingError(RuntimeError):
    """A ``RemoteAgent`` was used from an event loop other than the one it is bound to
    (the loop of its first I/O). Local; never translated. Create one agent per loop."""


LOOP_ERROR = "RemoteAgent is bound to another event loop; create one RemoteAgent per loop"

# Local sentinels: they pass through untranslated. Anything else raised during an RPC
# (an SDK bug on a malformed response included) becomes a generic TaskFailed, whose
# message never quotes the remote. CancelledError/KeyboardInterrupt are BaseExceptions
# and are never caught.
_LOCAL_SENTINELS = (A2AError, AgentClosedError, LoopBindingError)
# Remote JSON-RPC errors that mean the task is gone or cannot take this message.
_TASK_GONE = (
    sdk_errors.TaskNotFoundError,
    sdk_errors.UnsupportedOperationError,
    sdk_errors.TaskNotCancelableError,
)


@dataclass
class _StreamParts:
    """Per-stream part bookkeeping: was anything accepted, what was rejected first."""

    accepted: bool = False
    rejected: str | None = None


if sys.version_info >= (3, 11):
    _deadline = asyncio.timeout
else:  # pragma: no cover - Python 3.10 fallback

    @contextlib.asynccontextmanager
    async def _deadline(delay: float) -> AsyncIterator[None]:
        task = asyncio.current_task()
        fired = False

        def _fire() -> None:
            nonlocal fired
            fired = True
            task.cancel()

        handle = asyncio.get_running_loop().call_later(delay, _fire)
        try:
            yield
        except asyncio.CancelledError:
            if fired:
                raise TimeoutError from None
            raise
        finally:
            handle.cancel()


def _http_client(
    policy: Policy, headers: dict[str, str], *, allow_private: bool = False
) -> httpx.AsyncClient:
    """The egress-guarded client for one remote host.

    ``allow_private`` is only ever set by ``astromesh_a2a.testing.allow_loopback``.
    """
    return policy.client(headers=headers, allow_private=allow_private)


def _origin_key(url: str) -> tuple[str, str, int | None]:
    u = httpx.URL(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.scheme, (u.host or "").lower(), port


def _remote_text(text: str) -> str:
    """Remote-controlled text for an error message: short, one line."""
    text = " ".join(text.split())
    return text if len(text) <= _MAX_REMOTE_TEXT else text[:_MAX_REMOTE_TEXT] + "..."


def _card_fetch_url(card_url: str) -> str:
    """The card URL itself, or ``<base>/.well-known/agent-card.json`` for a base URL.

    A base URL (empty or ``/`` path) with a query or fragment is ambiguous: refused.
    """
    parts = urlsplit(card_url)
    if parts.path not in ("", "/"):
        return card_url
    if parts.query or parts.fragment or card_url.endswith(("?", "#")):
        raise PolicyRejected("url")
    return urlunsplit((parts.scheme, parts.netloc, WELL_KNOWN_CARD, "", ""))


def _json_data(data: Any) -> dict[str, Any] | None:
    """Outgoing ``data`` as a JSON object (str keys); TypeError if it is not one."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TypeError("data must be a dict")
    try:
        return json.loads(json.dumps(data, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"data is not JSON-serializable: {exc}") from None


def _parts_text(parts: Any) -> str:
    return "\n".join(p.text for p in parts if p.WhichOneof("content") == "text")


def _state_name(state: int) -> str:
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


class EventStream:
    """What ``RemoteAgent.stream()`` returns: an async iterator of ``Event``.

    Nothing is sent until the first ``__anext__``. ``trace`` holds the redacted trace so
    far. To stop early, use ``async with agent.stream(...) as events:`` or ``aclose()``:
    that closes the remote stream.
    """

    def __init__(self, events: Any, trace: Trace):
        self._events = events
        self._trace = trace

    @property
    def trace(self) -> list[dict[str, Any]]:
        return self._trace.to_dict()

    def __aiter__(self) -> EventStream:
        return self

    async def __anext__(self) -> Event:
        return await self._events.__anext__()

    async def aclose(self) -> None:
        await self._events.aclose()

    async def __aenter__(self) -> EventStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class RemoteAgent:
    """A remote A2A agent, reached only as far as ``policy`` allows.

    ``card_url`` is the card's URL, or the agent's base URL (then
    ``/.well-known/agent-card.json`` is appended). Use as ``async with RemoteAgent(...)``
    or call ``aclose()``.
    """

    def __init__(
        self,
        card_url: str,
        *,
        policy: Policy,
        auth: Auth | None = None,
        origin: Origin = Origin.AGENT,
    ):
        if not isinstance(policy, Policy):
            raise TypeError("policy must be a Policy")
        if auth is not None and not isinstance(auth, Auth):
            raise TypeError("auth must be an Auth")
        policy.check_card_url(card_url)
        self.card_url = card_url
        self.policy = policy
        self.auth = auth
        self.origin = Origin(origin)
        self._fetch_url = _card_fetch_url(card_url)
        self._lock = asyncio.Lock()
        self._card: AgentCard | None = None
        self._card_at = 0.0
        self._interface: AgentInterface | None = None
        self._card_http: httpx.AsyncClient | None = None
        self._rpc_http: httpx.AsyncClient | None = None
        self._retired: list[httpx.AsyncClient] = []  # replaced by a card refresh
        self._clients: dict[bool, Any] = {}
        self._contexts: OrderedDict[str, str] = OrderedDict()
        self._meter = BudgetMeter(policy.budget)
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None  # recorded on first I/O
        self._allow_private = False  # only astromesh_a2a.testing.allow_loopback sets it

    def __repr__(self) -> str:
        return f"RemoteAgent({self.card_url!r}, origin={self.origin.value!r})"

    async def __aenter__(self) -> RemoteAgent:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP clients this agent owns. Idempotent.

        Call it on the agent's own loop (from sync code: ``run_sync(agent.aclose(),
        agent=agent)``); from another, live loop it raises ``LoopBindingError``. If the
        agent's loop is already closed, its connections died with it: they are dropped.
        """
        bound = self._loop
        if bound is not None and bound is not asyncio.get_running_loop():
            if not bound.is_closed():
                raise LoopBindingError(LOOP_ERROR)
            self._closed = True
            self._clients = {}
            self._card_http = self._rpc_http = None
            self._retired = []
            return
        self._closed = True
        self._clients = {}  # SDK clients are dropped, never closed (would close our httpx)
        owned = (self._card_http, self._rpc_http, *self._retired)
        for hx in {id(c): c for c in owned if c}.values():
            await hx.aclose()
        self._card_http = self._rpc_http = None
        self._retired = []

    def _check_open(self) -> None:
        if self._closed:
            raise AgentClosedError("RemoteAgent is closed")

    def _bind_loop(self) -> None:
        """Record the running loop on first I/O; refuse any other loop afterwards.

        The httpx connections this agent pools belong to the loop that opened them.
        """
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise LoopBindingError(LOOP_ERROR)

    # ---- card -------------------------------------------------------------------

    async def card(self) -> RemoteCard:
        """The vetted agent card (cached ``policy.card_ttl`` seconds), as a ``RemoteCard``.

        It describes the one interface this client will use; ``.raw`` is a copy of the
        vetted SDK card. Its text was written by the remote: untrusted.
        """
        self._check_open()
        self._bind_loop()
        try:
            async with _deadline(self.policy.timeout):
                card = await self._vetted_card(Trace())
        except TimeoutError:
            raise A2ATimeout(f"card fetch exceeded {self.policy.timeout}s") from None
        # A vetted card under require_signature passed _verify_signature (else it raised).
        return RemoteCard.from_proto(card, signature_verified=self.policy.require_signature)

    async def _vetted_card(self, trace: Trace) -> AgentCard:
        async with self._lock:
            self._check_open()  # aclose() may have run while we waited for the lock
            now = time.monotonic()
            if self._card is not None and now - self._card_at < self.policy.card_ttl:
                trace.add("card", url=self._fetch_url, cached=True)
                return self._card
            previous = self._interface
            self._card = None  # a refresh that fails the checks leaves no stale card behind
            card = await self._load_card(trace)
            self._check_open()  # closed during the fetch: do not resurrect clients
            if previous is not None:  # refreshed: rebuild the SDK clients over the new card
                self._clients = {}
                assert self._interface is not None
                moved = _origin_key(previous.url) != _origin_key(self._interface.url)
                if moved and self._rpc_http is not None:
                    if self._rpc_http is not self._card_http:
                        self._retired.append(self._rpc_http)  # may still serve a stream
                    self._rpc_http = None
            self._card, self._card_at = card, now
            return card

    async def _load_card(self, trace: Trace) -> AgentCard:
        if self._card_http is None:
            self._card_http = self._new_http()
        headers = {"Accept": "application/json"}
        if self.auth is not None:
            # The card host is the one the caller configured and the policy allowed; some
            # servers (e.g. Nexus) authenticate the card too. Never traced.
            headers.update(self.auth.headers())
        try:
            response = await self._card_http.get(self._fetch_url, headers=headers)
        except httpx.TimeoutException:
            raise A2ATimeout("card fetch timed out") from None
        except httpx.HTTPError:
            raise PolicyRejected("card_fetch") from None
        if 300 <= response.status_code < 400:
            raise BlockedDestination(
                f"Blocked: card fetch answered {response.status_code} (redirects are not followed)"
            )
        if response.status_code != 200:
            raise PolicyRejected("card_fetch")
        try:
            raw = response.json()
            card = parse_agent_card(raw)
        except Exception:  # noqa: BLE001 - any parse failure is an invalid card
            raise PolicyRejected("card_invalid") from None
        if self.policy.require_signature:
            _verify_signature(card, self.policy)
        interface = _select_interface(card)
        self._check_interface_url(interface.url)
        vetted = AgentCard()
        vetted.CopyFrom(card)
        del vetted.supported_interfaces[:]
        vetted.supported_interfaces.append(interface)
        self._interface = interface
        trace.add(
            "card",
            url=self._fetch_url,
            name=card.name,
            interface=interface.url,
            protocol_version=interface.protocol_version,
            signed=bool(card.signatures),
        )
        return vetted

    def _check_interface_url(self, url: str) -> None:
        """The RPC URL must pass the policy too, unless it is on the card's own host."""
        try:
            self.policy.check_card_url(url)
        except PolicyRejected as exc:
            same_host = (urlsplit(url).hostname or "").lower() == (
                urlsplit(self.card_url).hostname or ""
            ).lower()
            if not (exc.reason == "host_not_allowed" and same_host):
                raise PolicyRejected(f"interface:{exc.reason}") from None

    def _new_http(self) -> httpx.AsyncClient:
        return _http_client(
            self.policy, {"A2A-Version": A2A_VERSION}, allow_private=self._allow_private
        )

    def _sdk_client(self, *, streaming: bool) -> Any:
        """SDK client over our pinned httpx client (one per streaming mode, reused)."""
        self._check_open()
        if streaming not in self._clients:
            assert self._card is not None
            assert self._interface is not None
            if self._rpc_http is None:
                if _origin_key(self._interface.url) == _origin_key(self._fetch_url):
                    self._rpc_http = self._card_http
                else:
                    self._rpc_http = self._new_http()
            config = ClientConfig(
                httpx_client=self._rpc_http,
                streaming=streaming,
                supported_protocol_bindings=[TransportProtocol.JSONRPC],
            )
            self._clients[streaming] = ClientFactory(config).create(self._card)
        return self._clients[streaming]

    # ---- send -------------------------------------------------------------------

    async def send(
        self, text: str, *, context_id: str | None = None, data: dict | None = None
    ) -> Result:
        """Send one message and wait for the task to finish or ask for input.

        A remote that asks for input (even "reply yes to confirm") gets no answer from
        here: the ``Result`` comes back ``input_required`` with the question in
        ``pending``; only an explicit ``reply()`` continues the task.
        """
        self._check_open()
        data = _json_data(data)
        trace = Trace()
        final = await self._guarded(
            lambda: self._send_blocking(text, context_id, data, trace), trace
        )
        return self._to_result(final, trace)

    async def reply(self, task_id: str, text: str) -> Result:
        """Answer a task that asked for input: a new message on the SAME task.

        This is the only way a pending task continues; the caller (a model or a human
        on this side) decides the answer.
        """
        self._check_open()
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a non-empty str")
        trace = Trace()
        context_id = self._contexts.get(task_id)
        final = await self._guarded(
            lambda: self._send_blocking(text, context_id, None, trace, task_id=task_id),
            trace,
        )
        return self._to_result(final, trace)

    async def cancel(self, task_id: str) -> None:
        """Ask the remote to cancel a task.

        Returns once the remote accepts the request: the task comes back ``canceled``, or
        still ``working`` when the remote cancels asynchronously (it may yet finish).
        ``TaskFailed`` if the task already ended in another final state.
        """
        self._check_open()
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a non-empty str")
        trace = Trace()
        task = await self._guarded(lambda: self._cancel(task_id, trace), trace)
        state = task.status.state
        if state in _FINAL_STATES and state != TaskState.TASK_STATE_CANCELED:
            raise TaskFailed(
                f"remote task could not be canceled (state {_state_name(state)})",
                remote=True,
            )

    def stream(self, text: str, *, context_id: str | None = None) -> EventStream:
        """Send one message over SSE and yield its events as they arrive.

        Ends after the first final status (completed, failed, canceled, rejected,
        input_required, auth_required) or a message. A failed/canceled task is an event,
        not an error. Capped by ``policy.timeout`` (whole stream, wall-clock) and
        ``policy.max_events``: past either, ``A2ATimeout`` and the stream is closed.
        The seconds charged to the budget are wall-clock from start to end of the stream,
        including the caller's own time between events.

        Parts the policy does not accept are dropped and traced: a status event is always
        emitted (empty ``text``/``data`` if its parts were dropped); an artifact or message
        event with nothing accepted is dropped. If the task completes having sent only
        rejected parts, ``PolicyRejected("part_kind:<kind>")`` instead of the completion.

        Use as ``async with agent.stream(...) as events: async for event in events``.
        Leaving early (``break``) only closes the remote stream when the ``async with``
        exits or ``aclose()`` is called; a bare ``async for`` + ``break`` leaves it to the
        garbage collector.
        """
        self._check_open()
        trace = Trace()
        return EventStream(self._stream(text, context_id, trace), trace)

    # ---- adapters -----------------------------------------------------------------

    def as_tool(
        self, framework: str, *, name: str | None = None, description: str | None = None
    ) -> Any:
        """This agent as a native tool of ``framework`` (``"langchain"``, ``"crewai"``,
        ``"adk"``, ``"openai-agents"``, ``"pydantic-ai"``, ``"llamaindex"``,
        ``"smolagents"``, ``"agent-framework"``, ``"mcp"``; needs that extra).

        ``name``/``description`` default to the card: ``await agent.card()`` first, or
        pass both. The tool keeps one conversation (and a pending task): build one per
        conversation. See ``astromesh_a2a.adapters``.
        """
        from astromesh_a2a.adapters import as_tool

        return as_tool(self, framework, name=name, description=description)

    # ---- plumbing -----------------------------------------------------------------

    def _limit(self) -> float:
        """Wall-clock cap for one call: the policy timeout, or the budget left if less."""
        left = self._meter.remaining_seconds()
        return self.policy.timeout if left is None else min(self.policy.timeout, left)

    async def _guarded(self, run: Callable[[], Awaitable[_T]], trace: Trace) -> _T:
        """Budget, deadline, vetted card, then ``run()``; failures become our errors (only
        our local sentinels pass through)."""
        self._bind_loop()
        self._meter.admit()
        limit = self._limit()
        started = time.monotonic()
        cut = False  # a call cut by its deadline spent the whole limit
        try:
            try:
                async with _deadline(limit):
                    await self._vetted_card(trace)
                    return await run()
            except TimeoutError:
                cut = True
                raise A2ATimeout(f"remote call exceeded {limit:g}s") from None
            except _LOCAL_SENTINELS:
                raise
            except Exception as exc:  # noqa: BLE001 - translated below, never passed through
                failure = exc
            raise _translated(failure)  # outside the handler: no implicit __context__ cycle
        finally:
            elapsed = time.monotonic() - started
            self._meter.record_seconds(max(elapsed, limit) if cut else elapsed)

    def _call_context(self, trace: Trace) -> tuple[ClientCallContext, str]:
        traceparent = trace.traceparent()
        headers = {"traceparent": traceparent}
        if self.auth is not None:
            headers.update(self.auth.headers())
        context = ClientCallContext(service_parameters=headers, timeout=self.policy.timeout)
        return context, traceparent

    def _message_request(
        self,
        text: str,
        context_id: str | None,
        data: dict | None,
        task_id: str | None,
        traceparent: str,
    ) -> SendMessageRequest:
        message = Message(
            role=Role.ROLE_USER,
            message_id=uuid.uuid4().hex,
            context_id=context_id or "",
            task_id=task_id or "",
            parts=[Part(text=text)],
        )
        if data is not None:
            part = Part()
            part.data.struct_value.update(data)
            message.parts.append(part)
        message.metadata.update({"origin": self.origin.value, "traceparent": traceparent})
        return SendMessageRequest(message=message)

    async def _send_blocking(
        self,
        text: str,
        context_id: str | None,
        data: dict | None,
        trace: Trace,
        *,
        task_id: str | None = None,
    ) -> StreamResponse:
        context, traceparent = self._call_context(trace)
        request = self._message_request(text, context_id, data, task_id, traceparent)
        fields: dict[str, Any] = {"task_id": task_id} if task_id else {}
        trace.add(
            "send",
            method="SendMessage",
            origin=self.origin.value,
            context_id=context_id,
            **fields,
        )
        client = self._sdk_client(streaming=False)
        final = None
        async for event in client.send_message(request, context=context):
            final = event
        if final is None:
            raise TaskFailed("remote returned no response")
        return final

    async def _cancel(self, task_id: str, trace: Trace) -> Any:
        context, _ = self._call_context(trace)
        trace.add("send", method="CancelTask", task_id=task_id)
        client = self._sdk_client(streaming=False)
        return await client.cancel_task(CancelTaskRequest(id=task_id), context=context)

    async def _stream(
        self, text: str, context_id: str | None, trace: Trace
    ) -> AsyncIterator[Event]:
        self._check_open()
        self._bind_loop()
        self._meter.admit()
        limit = self._limit()
        started = time.monotonic()
        cut = False  # a call cut by its deadline spent the whole limit
        deadline = started + limit
        events: Any = None
        count = 0
        parts = _StreamParts()
        try:
            try:
                async with _deadline(limit):
                    await self._vetted_card(trace)
                context, traceparent = self._call_context(trace)
                request = self._message_request(text, context_id, None, None, traceparent)
                trace.add(
                    "send",
                    method="SendStreamingMessage",
                    origin=self.origin.value,
                    context_id=context_id,
                )
                events = self._sdk_client(streaming=True).send_message(request, context=context)
                while True:
                    # The deadline covers the waits on the remote, never the caller's code
                    # (a yield inside a timeout block would cancel the caller instead).
                    async with _deadline(max(0.0, deadline - time.monotonic())):
                        try:
                            raw = await events.__anext__()
                        except StopAsyncIteration:
                            break
                    count += 1
                    if count > self.policy.max_events:
                        raise A2ATimeout(f"stream exceeded {self.policy.max_events} events")
                    out, stop = self._to_events(raw, trace, parts)
                    for event in out:
                        yield event
                    if stop:
                        break
            except TimeoutError:
                cut = True
                raise A2ATimeout(f"stream exceeded {limit:g}s") from None
            except _LOCAL_SENTINELS:
                raise
            except Exception as exc:  # noqa: BLE001 - translated below, never passed through
                failure = exc
            else:
                return
            raise _translated(failure)
        finally:
            if events is not None:
                with contextlib.suppress(Exception):
                    await events.aclose()
            elapsed = time.monotonic() - started
            self._meter.record_seconds(max(elapsed, limit) if cut else elapsed)

    # ---- events -----------------------------------------------------------------

    def _to_events(
        self, raw: StreamResponse, trace: Trace, parts: _StreamParts
    ) -> tuple[list[Event], bool]:
        """Our events for one SDK event, and whether the stream ends with it."""
        kind = raw.WhichOneof("payload")
        if kind == "task":
            task = raw.task
            out = [
                self._artifact_event(a, task.id, task.context_id, False, True, trace, parts)
                for a in task.artifacts
            ]
            status = self._status_event(task.status, task.id, task.context_id, trace, parts)
            return [e for e in (*out, status) if e is not None], status.payload["final"]
        if kind == "status_update":
            u = raw.status_update
            status = self._status_event(u.status, u.task_id, u.context_id, trace, parts)
            return [status], status.payload["final"]
        if kind == "artifact_update":
            a = raw.artifact_update
            event = self._artifact_event(
                a.artifact, a.task_id, a.context_id, a.append, a.last_chunk, trace, parts
            )
            return ([event] if event else []), False
        if kind == "message":
            m = raw.message
            trace.add("message", task_id=m.task_id or None, parts=len(m.parts))
            text, data = self._stream_parts(m.parts, trace, parts, content=True)
            self._check_completion(parts)  # a message ends the stream
            if not (text or data) and m.parts:
                return [], True
            return [Event("message", self._payload(m.task_id, m.context_id, text, data))], True
        raise TaskFailed(f"unexpected stream event {kind!r}")

    def _stream_parts(
        self, raw_parts: Any, trace: Trace, parts: _StreamParts, *, content: bool
    ) -> tuple[str, list[dict[str, Any]]]:
        """``_accept_parts`` plus the stream's bookkeeping (never raises for kinds)."""
        text, data, rejected = self._accept_parts(raw_parts, trace)
        if rejected and parts.rejected is None:
            parts.rejected = rejected[0]
        if content and (text or data):
            parts.accepted = True
        return text, data

    @staticmethod
    def _check_completion(parts: _StreamParts) -> None:
        """At completion: only rejected parts ever arrived → ``PolicyRejected``."""
        if parts.rejected is not None and not parts.accepted:
            raise PolicyRejected(f"part_kind:{parts.rejected}")

    def _payload(
        self, task_id: str, context_id: str, text: str, data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self._remember(task_id, context_id)
        return {
            "task_id": task_id or None,
            "context_id": context_id or None,
            "text": text,
            "data": data,
            "untrusted": True,
        }

    def _status_event(
        self,
        status: TaskStatus,
        task_id: str,
        context_id: str,
        trace: Trace,
        parts: _StreamParts,
    ) -> Event:
        state = _state_name(status.state)
        trace.add("status", state=state, task_id=task_id or None)
        completed = status.state == TaskState.TASK_STATE_COMPLETED
        # As in send(): a completion's own message counts as content.
        text, data = self._stream_parts(status.message.parts, trace, parts, content=completed)
        if completed:
            self._check_completion(parts)
        payload = self._payload(task_id, context_id, text, data)
        payload.update(state=state, final=status.state in _FINAL_STATES)
        return Event("status", payload)

    def _artifact_event(
        self,
        artifact: Any,
        task_id: str,
        context_id: str,
        append: bool,
        last_chunk: bool,
        trace: Trace,
        parts: _StreamParts,
    ) -> Event | None:
        artifact_id = artifact.artifact_id or None
        trace.add("artifact", artifact_id=artifact_id, parts=len(artifact.parts))
        text, data = self._stream_parts(artifact.parts, trace, parts, content=True)
        if not (text or data) and artifact.parts:
            return None  # nothing accepted: dropped (the rejection is in the trace)
        payload = self._payload(task_id, context_id, text, data)
        payload.update(
            artifact_id=artifact_id,
            name=artifact.name or None,
            append=bool(append),
            last_chunk=bool(last_chunk),
        )
        return Event("artifact", payload)

    def _remember(self, task_id: str, context_id: str) -> None:
        if task_id and context_id:
            self._contexts[task_id] = context_id
            self._contexts.move_to_end(task_id)
            while len(self._contexts) > _MAX_CONTEXTS:
                self._contexts.popitem(last=False)

    # ---- result -----------------------------------------------------------------

    def _to_result(self, event: StreamResponse, trace: Trace) -> Result:
        kind = event.WhichOneof("payload")
        if kind == "message":
            msg = event.message
            answer, data = self._accept(msg.parts, trace)
            return self._finish(trace, "completed", answer, data, msg.task_id, msg.context_id, None)
        if kind != "task":
            raise TaskFailed(f"unexpected final event {kind!r}")
        task = event.task
        state = task.status.state
        name = TaskState.Name(state).removeprefix("TASK_STATE_")
        status_text = _parts_text(task.status.message.parts)
        if state == TaskState.TASK_STATE_COMPLETED:
            parts = [p for a in task.artifacts for p in a.parts] or list(task.status.message.parts)
            answer, data = self._accept(parts, trace)
            return self._finish(trace, "completed", answer, data, task.id, task.context_id, None)
        if state in (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED):
            question, _ = self._accept(task.status.message.parts, trace)
            return self._finish(
                trace, name.lower(), "", [], task.id, task.context_id, question or None
            )
        if state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
            raise A2ATimeout(f"remote task did not finish (state {name})")
        detail = f": {_remote_text(status_text)}" if status_text else ""
        raise TaskFailed(f"remote task {name}{detail}", remote=True)

    def _finish(
        self,
        trace: Trace,
        status: str,
        answer: str,
        data: list[dict[str, Any]],
        task_id: str,
        context_id: str,
        pending: str | None,
    ) -> Result:
        trace.add("result", status=status, task_id=task_id or None, parts=len(data))
        self._remember(task_id, context_id)
        return Result(
            status=status,
            answer=answer,
            data=data,
            task_id=task_id or None,
            context_id=context_id or None,
            pending=pending,
            trace=trace.to_dict(),
        )

    def _accept(self, parts: Any, trace: Trace) -> tuple[str, list[dict[str, Any]]]:
        """Apply ``accept_parts``/``max_bytes`` to each part; text joined, rest as data.

        A part of a kind the policy does not accept is DROPPED (noted in the trace as a
        ``rejection``); ``PolicyRejected`` only when parts existed and none was accepted.
        An oversized part is always a hard ``PolicyRejected("part_size")``.
        """
        text, data, rejected = self._accept_parts(parts, trace)
        if rejected and not text and not data:
            raise PolicyRejected(f"part_kind:{rejected[0]}")
        return text, data

    def _accept_parts(
        self, parts: Any, trace: Trace
    ) -> tuple[str, list[dict[str, Any]], list[str]]:
        """Like ``_accept`` but never raises for rejected kinds: also returns them."""
        texts: list[str] = []
        data: list[dict[str, Any]] = []
        rejected: list[str] = []
        for part in parts:
            kind = part.WhichOneof("content")
            policy_kind = {"raw": "file", "url": "file"}.get(kind or "", kind)
            if policy_kind is None:
                continue
            if policy_kind not in self.policy.accept_parts:
                rejected.append(policy_kind)
                trace.add("rejection", kind=policy_kind, reason=f"part_kind:{policy_kind}")
                continue
            if kind == "text":
                self.policy.check_part("text", len(part.text.encode()))
                texts.append(part.text)
            elif kind == "data":
                value = MessageToDict(part.data)
                self.policy.check_part("data", len(json.dumps(value).encode()))
                data.append(value if isinstance(value, dict) else {"value": value})
            elif kind == "raw":
                self.policy.check_part("file", len(part.raw))
                data.append(
                    {
                        "file": {
                            "bytes_b64": base64.b64encode(part.raw).decode(),
                            "media_type": part.media_type,
                            "filename": part.filename,
                        }
                    }
                )
            elif kind == "url":
                self.policy.check_part("file", len(part.url.encode()))
                data.append(
                    {
                        "file": {
                            "url": part.url,
                            "media_type": part.media_type,
                            "filename": part.filename,
                        }
                    }
                )
        return "\n".join(texts), data, rejected


def _select_interface(card: AgentCard) -> AgentInterface:
    """The JSON-RPC interface speaking protocol 1.x (prefer 1.0); else UnsupportedVersion."""
    jsonrpc = [i for i in card.supported_interfaces if i.protocol_binding == "JSONRPC"]
    if not jsonrpc:
        raise UnsupportedVersion("remote card has no JSON-RPC interface")
    v1 = [i for i in jsonrpc if i.protocol_version.split(".")[0] == "1"]
    if not v1:
        versions = sorted({i.protocol_version or "(none)" for i in jsonrpc})
        shown = ", ".join(_remote_text(v) for v in versions[:_MAX_VERSIONS_SHOWN])
        more = ", ..." if len(versions) > _MAX_VERSIONS_SHOWN else ""
        raise UnsupportedVersion(f"remote speaks A2A {shown}{more}; need 1.x")
    exact = [i for i in v1 if i.protocol_version in ("1.0", "1.0.0")]
    return (exact or v1)[0]


def _verify_signature(card: AgentCard, policy: Policy) -> None:
    """Verify the card's JWS signatures with keys pinned by ``kid`` (``jku`` is ignored)."""
    try:
        import jwt
        from a2a.utils.signing import create_signature_verifier
    except ImportError:
        raise PolicyRejected("signature") from ImportError(
            "card signatures need PyJWT: pip install 'astromesh-a2a[signing]'"
        )
    keys = policy.signing_keys
    if not keys:
        raise PolicyRejected("signature")

    def key_provider(kid: str | None, jku: str | None) -> Any:
        if kid is None or kid not in keys:
            raise jwt.exceptions.InvalidKeyError("unknown signing key id")
        return keys[kid]

    verifier = create_signature_verifier(key_provider, list(policy.signing_algorithms))
    try:
        verifier(card)
    except Exception:  # noqa: BLE001 - unsigned, tampered or unverifiable: all rejected
        raise PolicyRejected("signature") from None


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` and its causes/contexts, each once (safe on cyclic chains)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _translated(exc: BaseException) -> A2AError:
    """Our error for ``exc``, to be raised OUTSIDE the ``except`` block.

    One of ours already in ``exc``'s chain comes back untouched: chaining it to ``exc``
    (``from exc``, or raising it inside the handler, which sets ``__context__``) would
    make the chain cyclic. A new error gets ``exc`` as its ``__cause__``.
    """
    error = _translate(exc)
    if not any(e is error for e in _chain(exc)):
        error.__cause__ = exc
    return error


def _translate(exc: BaseException) -> A2AError:
    """Map an SDK/httpx failure to our errors, without passing remote text through."""
    for seen in _chain(exc):
        if isinstance(seen, A2AError):
            return seen
    if isinstance(exc, sdk_errors.VersionNotSupportedError):
        return UnsupportedVersion(f"remote rejected A2A-Version {A2A_VERSION}")
    if isinstance(exc, sdk_errors.ContentTypeNotSupportedError):
        return PolicyRejected("content_type")
    if isinstance(exc, (A2AClientTimeoutError, httpx.TimeoutException)):
        return A2ATimeout("remote call timed out")
    for cause in _chain(exc):
        if isinstance(cause, _TASK_GONE):  # the remote says: no such (live) task
            return TaskFailed(f"remote task is not available ({type(cause).__name__})", remote=True)
    for cause in _chain(exc):
        if isinstance(cause, httpx.HTTPStatusError):
            return TaskFailed(f"remote answered HTTP {cause.response.status_code}")
    return TaskFailed(f"remote error: {type(exc).__name__}")
