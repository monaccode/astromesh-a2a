"""Policy for talking to remote agents: what is allowed, how much, for how long."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import httpx

from astromesh_a2a import net
from astromesh_a2a.errors import PolicyRejected


@dataclass(frozen=True)
class Budget:
    """Cap on calls and/or wall-clock seconds spent on one remote.

    Seconds are wall-clock per call; for ``stream()`` that is start to end of the stream,
    including the caller's own time between events. A call is admitted only while some
    seconds are left and its deadline is capped at what is left, but concurrent calls on
    the same remote each see the same remainder: under concurrency the seconds cap is
    soft (it can be overrun by up to the calls in flight). ``max_calls`` is exact.
    """

    max_calls: int | None = None
    max_seconds: float | None = None


@dataclass(frozen=True)
class Policy:
    """Client-side policy. The default allows nothing until hosts are declared.

    ``allowed`` entries are either bare hosts or full card URLs. Host entries match the
    URL's hostname exactly, case-insensitively: no subdomain or suffix matching, the port
    is ignored, and a trailing dot ("a.com.") is a different host. URL entries must match
    the whole URL exactly (case-sensitive).

    Card signatures (``require_signature=True``) are verified against ``signing_keys``,
    a map ``kid -> key`` (PEM/secret str or bytes, or a ``jwt.PyJWK``), with only the
    ``signing_algorithms`` listed. Keys are pinned by ``kid``: a ``jku`` URL in the
    (untrusted) card header is never fetched. Needs the ``signing`` extra (PyJWT).

    ``max_bytes`` caps every response body and every part. A stream (SSE) is capped at
    ``max_stream_bytes`` in total and ``max_bytes`` per event, and also by ``timeout``
    (wall-clock for the whole stream) and ``max_events``. SSE lines must end in LF (or
    CRLF): a stream framed with bare CR reads as one oversized event and is refused
    (``BlockedDestination``). The vetted card is cached for ``card_ttl`` seconds, then
    fetched and vetted again.
    """

    allowed: tuple[str, ...] = ()
    require_signature: bool = False
    accept_parts: frozenset[str] = field(default_factory=lambda: frozenset({"text", "data"}))
    max_bytes: int = 1_048_576
    timeout: float = 60.0
    max_events: int = 1000
    budget: Budget | None = None
    max_stream_bytes: int = 8_388_608
    card_ttl: float = 300.0
    allow_insecure_http: bool = False
    signing_keys: Mapping[str, Any] = field(default_factory=dict, hash=False, repr=False)
    signing_algorithms: tuple[str, ...] = ("ES256", "ES384", "RS256", "PS256", "EdDSA")

    def __post_init__(self) -> None:
        if isinstance(self.allowed, str):
            raise TypeError("Policy.allowed must be a sequence of hosts/URLs, not a bare str")
        if isinstance(self.accept_parts, str):
            raise TypeError("Policy.accept_parts must be a collection of kinds, not a bare str")
        object.__setattr__(self, "allowed", tuple(self.allowed))
        object.__setattr__(self, "accept_parts", frozenset(self.accept_parts))
        if isinstance(self.signing_algorithms, str):
            raise TypeError("Policy.signing_algorithms must be a sequence, not a bare str")
        object.__setattr__(self, "signing_algorithms", tuple(self.signing_algorithms))
        object.__setattr__(self, "signing_keys", MappingProxyType(dict(self.signing_keys)))
        if self.card_ttl < 0:
            raise ValueError("Policy.card_ttl must be >= 0")

    def check_card_url(self, url: str) -> None:
        """Raise PolicyRejected unless ``url`` is https (or opted-in http) and allowed."""
        if not isinstance(url, str) or any(
            c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url
        ):
            raise PolicyRejected("url")
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError:
            raise PolicyRejected("url") from None
        schemes = {"https", "http"} if self.allow_insecure_http else {"https"}
        if parts.scheme.lower() not in schemes:
            raise PolicyRejected("scheme")
        if not host:
            raise PolicyRejected("host")
        for entry in self.allowed:
            if "://" in entry:
                if entry == url:
                    return
            elif entry.lower() == host:
                return
        raise PolicyRejected("host_not_allowed")

    def check_part(self, kind: str, size: int) -> None:
        """Raise PolicyRejected if a part of this kind/size must not be accepted."""
        if kind not in self.accept_parts:
            raise PolicyRejected(f"part_kind:{kind}")
        if size > self.max_bytes:
            raise PolicyRejected("part_size")

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        """An egress-guarded client with this policy's timeout and size cap."""
        return net.safe_client(
            timeout=self.timeout,
            max_bytes=self.max_bytes,
            max_stream_bytes=self.max_stream_bytes,
            **kwargs,
        )


class BudgetMeter:
    """Tracks calls and seconds used against a Budget (one per remote)."""

    def __init__(self, budget: Budget | None):
        self.budget = budget
        self.calls = 0
        self.seconds = 0.0

    def charge_call(self) -> None:
        self.calls += 1
        b = self.budget
        if b and b.max_calls is not None and self.calls > b.max_calls:
            raise PolicyRejected("budget")

    def admit(self) -> None:
        """Charge one call BEFORE it starts; ``PolicyRejected("budget")`` if none is left.

        Refused when the calls are used up or the seconds already spent reach the cap. A
        refused call is not counted.
        """
        b = self.budget
        if b and b.max_seconds is not None and self.seconds >= b.max_seconds:
            raise PolicyRejected("budget")
        if b and b.max_calls is not None and self.calls >= b.max_calls:
            raise PolicyRejected("budget")
        self.calls += 1

    def remaining_seconds(self) -> float | None:
        """Seconds left in the budget (``None`` if seconds are not capped)."""
        b = self.budget
        if b is None or b.max_seconds is None:
            return None
        return max(0.0, b.max_seconds - self.seconds)

    def record_seconds(self, s: float) -> None:
        """Add the time a call took; never raises (the call already happened)."""
        self.seconds += max(0.0, s)

    def charge_seconds(self, s: float) -> None:
        if s < 0:
            raise ValueError("seconds must be >= 0")
        self.seconds += s
        b = self.budget
        if b and b.max_seconds is not None and self.seconds > b.max_seconds:
            raise PolicyRejected("budget")
