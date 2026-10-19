"""Safe egress: where a remote-agent request may go.

Every outbound request (card fetch, RPC, stream) goes through ``PinnedTransport``:
only http/https, only PUBLIC hosts, the host is resolved ONCE and the connection is
pinned to the checked IP (no DNS-rebinding window), no redirects, and the body is
capped on decoded bytes. Guard failures raise ``BlockedDestination`` (not an httpx
exception) so it surfaces unchanged through the a2a-sdk client.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from astromesh_a2a.errors import BlockedDestination

_INTERNAL_SUFFIXES = (".local", ".internal", ".svc", ".cluster.local", ".localhost")

# NAT64 well-known prefix (RFC 6052): judge the embedded IPv4, not the whole prefix.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

Resolver = Callable[[str, "int | None"], Awaitable[list]]


async def _system_resolver(host: str, port: int | None) -> list:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port)


def _non_global(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return not ip.is_global


def _is_internal_name(host: str) -> bool:
    return host == "localhost" or "." not in host or host.endswith(_INTERNAL_SUFFIXES)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def blocked_reason(url: str) -> str | None:
    """Why ``url`` may not be requested, judged WITHOUT resolving DNS; None if it may.

    Covers scheme, missing host, non-public IP literals and internal host names.
    Names that resolve to private IPs are caught by ``PinnedTransport``.
    """
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL:
        return "Blocked: invalid URL"
    if u.scheme not in ("http", "https"):
        return f"Blocked: scheme {u.scheme or '(none)'!r} is not allowed"
    host = u.host.rstrip(".").lower() if u.host else ""
    if not host:
        return "Blocked: URL without a host"
    if _is_ip(host):
        return f"Blocked: {host} is not a public address" if _non_global(host) else None
    if _is_internal_name(host):
        return f"Blocked: {host} is an internal host"
    return None


async def pin_to_public_ip(
    url: str, resolver: Resolver | None = None
) -> tuple[str, str, str] | None:
    """Resolve the host of ``url`` once and return ``(pinned_url, sni, host_header)``.

    None when there is no name to pin (IP literal or internal name; ``blocked_reason``
    decides those). Raises ``BlockedDestination`` if the name does not resolve or ANY
    address is non-public.
    """
    u = httpx.URL(url)
    host = u.raw_host.decode("ascii") if u.raw_host else ""
    if not host or _is_ip(host) or _is_internal_name(host):
        return None
    try:
        infos = await (resolver or _system_resolver)(host, u.port)
    except OSError as exc:
        raise BlockedDestination(f"Blocked: {host} does not resolve") from exc
    addresses = [str(info[4][0]) for info in infos]
    if not addresses or any(_non_global(a) for a in addresses):
        raise BlockedDestination(f"Blocked: {host} resolves to a non-public address")
    pinned = str(u.copy_with(host=addresses[0]))
    default_port = 443 if u.scheme == "https" else 80
    host_header = host if not u.port or u.port == default_port else f"{host}:{u.port}"
    return pinned, host, host_header


class _BoundedBody(httpx.AsyncByteStream):
    """Response stream cut as soon as the decoded byte count passes the cap."""

    def __init__(self, stream: Any, max_bytes: int, transport: PinnedTransport):
        self._stream = stream
        self._max = max_bytes
        self._transport = transport

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        async for chunk in self._stream:
            total += len(chunk)
            if total > self._max:
                raise self._transport._fail(f"Blocked: response exceeds {self._max} bytes")
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    """httpx transport that enforces the egress guard.

    Resolves each host ONCE per transport and requires every IP to be global; connects
    to the checked IP keeping the original ``Host`` and SNI; asks for
    ``Accept-Encoding: identity`` and rejects any other content-encoding (so the cap
    counts what the client reads); does not follow redirects (a 3xx is noted in
    ``reason``). One transport serves ONE host. ``reason`` holds the FIRST guard
    failure (never a plain 4xx/5xx).
    """

    def __init__(
        self,
        max_bytes: int = 1_048_576,
        inner: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        allow_private: bool = False,
    ):
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.max_bytes = max_bytes
        self._resolver = resolver
        self._allow_private = allow_private
        self._pins: dict[tuple, tuple[str, str, str] | None] = {}
        self.reason: str | None = None

    def _note(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason

    def _fail(self, reason: str) -> BlockedDestination:
        self._note(reason)
        return BlockedDestination(reason)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.url.scheme, request.url.raw_host, request.url.port)
        if self._pins and key not in self._pins:
            raise self._fail(
                f"Blocked: this transport serves only one host, not {request.url.host}"
            )
        if key not in self._pins:
            pin = None
            if not self._allow_private:
                reason = blocked_reason(str(request.url))
                if reason:
                    raise self._fail(reason)
                try:
                    pin = await pin_to_public_ip(str(request.url), self._resolver)
                except BlockedDestination as exc:
                    raise self._fail(exc.reason) from exc
            self._pins[key] = pin
        pin = self._pins[key]
        if pin is not None:
            pinned_url, sni, host_header = pin
            request.url = request.url.copy_with(host=httpx.URL(pinned_url).host)
            request.headers["Host"] = host_header
            if request.url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": sni}
        request.headers["Accept-Encoding"] = "identity"
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.TimeoutException as exc:
            self._note(f"timeout: {exc}")
            raise
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding != "identity":
            await response.aclose()
            raise self._fail(f"Blocked: response has content-encoding {encoding}")
        if 300 <= response.status_code < 400:
            self._note(f"server answered {response.status_code} (redirects are not followed)")
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=_BoundedBody(response.stream, self.max_bytes, self),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def safe_client(
    *, timeout: float, max_bytes: int, allow_private: bool = False, **kwargs: Any
) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` whose every request passes the egress guard.

    No redirects. ``allow_private`` lifts only the public-address rule (for a
    deployment that opts in on purpose); the body cap and no-redirect rules stay.
    """
    return httpx.AsyncClient(
        transport=PinnedTransport(max_bytes=max_bytes, allow_private=allow_private),
        timeout=timeout,
        follow_redirects=False,
        **kwargs,
    )
