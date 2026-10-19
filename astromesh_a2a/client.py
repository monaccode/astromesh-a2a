"""``RemoteAgent``: talk to a remote A2A agent through the egress guard and the Policy.

Flow of a call (ADR 0001 §1, §5):

1. ``Policy.check_card_url`` runs in the constructor: an unlisted host never sees a request.
2. The card is fetched by US through a pinned ``net.safe_client`` (never by the SDK), parsed
   with the SDK's ``parse_agent_card`` and vetted: signature (if required), protocol
   version (only a 1.x JSON-RPC interface; the SDK would silently fall back to 0.3) and the
   interface URL (same host as the card, or itself allowed by the policy).
3. An SDK ``Client`` is built over OUR httpx client from a copy of the card that keeps
   only the vetted interface, so the SDK cannot pick another one.
4. Every SDK call runs under ``policy.timeout`` wall-clock, on top of httpx timeouts.

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
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client import A2AClientTimeoutError, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.types import (
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskState,
)
from a2a.utils import errors as sdk_errors
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict

from astromesh_a2a.errors import (
    A2AError,
    A2ATimeout,
    BlockedDestination,
    PolicyRejected,
    TaskFailed,
    UnsupportedVersion,
)
from astromesh_a2a.policy import Policy
from astromesh_a2a.trace import Trace
from astromesh_a2a.types import Auth, Origin, Result

WELL_KNOWN_CARD = "/.well-known/agent-card.json"
A2A_VERSION = "1.0"
_MAX_REMOTE_TEXT = 200
_MAX_VERSIONS_SHOWN = 5

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


def _http_client(policy: Policy, headers: dict[str, str]) -> httpx.AsyncClient:
    """The egress-guarded client for one remote host. (Tests swap this for loopback.)"""
    return policy.client(headers=headers)


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
        self._interface: AgentInterface | None = None
        self._card_http: httpx.AsyncClient | None = None
        self._rpc_http: httpx.AsyncClient | None = None
        self._clients: dict[bool, Any] = {}
        self._closed = False

    def __repr__(self) -> str:
        return f"RemoteAgent({self.card_url!r}, origin={self.origin.value!r})"

    async def __aenter__(self) -> RemoteAgent:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP clients this agent owns. Idempotent."""
        self._closed = True
        self._clients = {}  # SDK clients are dropped, never closed (would close our httpx)
        for hx in {id(c): c for c in (self._card_http, self._rpc_http) if c}.values():
            await hx.aclose()
        self._card_http = self._rpc_http = None

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("RemoteAgent is closed")

    # ---- card -------------------------------------------------------------------

    async def card(self) -> AgentCard:
        """The vetted agent card (fetched once, then cached). Returns a copy."""
        self._check_open()
        try:
            async with _deadline(self.policy.timeout):
                card = await self._vetted_card(Trace())
        except TimeoutError:
            raise A2ATimeout(f"card fetch exceeded {self.policy.timeout}s") from None
        copy = AgentCard()
        copy.CopyFrom(card)
        return copy

    async def _vetted_card(self, trace: Trace) -> AgentCard:
        async with self._lock:
            if self._card is None:
                self._card = await self._load_card(trace)
            else:
                trace.add("card", url=self._fetch_url, cached=True)
            return self._card

    async def _load_card(self, trace: Trace) -> AgentCard:
        if self._card_http is None:
            self._card_http = _http_client(self.policy, {"A2A-Version": A2A_VERSION})
        try:
            response = await self._card_http.get(
                self._fetch_url, headers={"Accept": "application/json"}
            )
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

    def _sdk_client(self, *, streaming: bool) -> Any:
        """SDK client over our pinned httpx client (one per streaming mode, reused)."""
        if streaming not in self._clients:
            assert self._card is not None
            assert self._interface is not None
            if self._rpc_http is None:
                if _origin_key(self._interface.url) == _origin_key(self._fetch_url):
                    self._rpc_http = self._card_http
                else:
                    self._rpc_http = _http_client(self.policy, {"A2A-Version": A2A_VERSION})
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
        """Send one message and wait for the task to finish or ask for input."""
        self._check_open()
        data = _json_data(data)
        trace = Trace()
        try:
            async with _deadline(self.policy.timeout):
                await self._vetted_card(trace)
                final = await self._send_blocking(text, context_id, data, trace)
        except TimeoutError:
            raise A2ATimeout(f"remote call exceeded {self.policy.timeout}s") from None
        except A2AError:
            raise
        except Exception as exc:  # noqa: BLE001 - translated below, never passed through
            failure = exc
        else:
            return self._to_result(final, trace)
        raise _translated(failure)  # outside the handler: no implicit __context__ cycle

    async def _send_blocking(
        self, text: str, context_id: str | None, data: dict | None, trace: Trace
    ) -> StreamResponse:
        traceparent = trace.traceparent()
        headers = {"traceparent": traceparent}
        if self.auth is not None:
            headers.update(self.auth.headers())
        message = Message(
            role=Role.ROLE_USER,
            message_id=uuid.uuid4().hex,
            context_id=context_id or "",
            parts=[Part(text=text)],
        )
        if data is not None:
            part = Part()
            part.data.struct_value.update(data)
            message.parts.append(part)
        message.metadata.update({"origin": self.origin.value, "traceparent": traceparent})
        request = SendMessageRequest(message=message)
        context = ClientCallContext(service_parameters=headers, timeout=self.policy.timeout)
        trace.add("send", method="SendMessage", origin=self.origin.value, context_id=context_id)
        client = self._sdk_client(streaming=False)
        final = None
        async for event in client.send_message(request, context=context):
            final = event
        if final is None:
            raise TaskFailed("remote returned no response")
        return final

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
        raise TaskFailed(f"remote task {name}{detail}")

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
        if rejected and not texts and not data:
            raise PolicyRejected(f"part_kind:{rejected[0]}")
        return "\n".join(texts), data


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
        if isinstance(cause, httpx.HTTPStatusError):
            return TaskFailed(f"remote answered HTTP {cause.response.status_code}")
    return TaskFailed(f"remote error: {type(exc).__name__}")
