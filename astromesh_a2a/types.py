"""Our public value types (no protobuf crosses this boundary)."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from astromesh_a2a.errors import PolicyRejected


class Origin(str, Enum):
    """Who produced a turn. Sent as ``origin`` in the A2A message metadata."""

    HUMAN = "human"
    AGENT = "agent"


@dataclass
class Result:
    """Outcome of a remote call.

    ``status`` is ``"completed"``, ``"input_required"`` or ``"auth_required"``. On
    completion ``answer`` joins the text parts and ``data`` holds the data parts; when the
    remote asks for something, ``pending`` holds its question (never auto-answered).
    ``trace`` is the redacted per-stage trace of the call.
    """

    status: str
    answer: str
    data: list[dict[str, Any]]
    task_id: str | None
    context_id: str | None
    pending: str | None
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Event:
    """One event of ``RemoteAgent.stream()``, already normalized (no protobuf).

    ``kind`` is ``"status"``, ``"artifact"`` or ``"message"``. Every ``payload`` has
    ``task_id``, ``context_id``, ``text`` (accepted text parts, joined), ``data`` (accepted
    data/file parts) and ``untrusted=True``: it was written by the remote. A ``status``
    payload adds ``state`` (``"working"``, ``"completed"``, ``"input_required"``...) and
    ``final``; an ``artifact`` payload adds ``artifact_id``, ``name``, ``append`` and
    ``last_chunk``.
    """

    kind: Literal["status", "artifact", "message"]
    payload: dict[str, Any]


@dataclass(frozen=True)
class Auth:
    """Credentials for the remote's RPC endpoint, read at call time.

    The secret comes from an environment variable or a callable; it is never stored on
    this object, never put in a trace, repr or error message.
    """

    header: str
    env: str | None = None
    prefix: str = ""
    source: Callable[[], str | None] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def api_key(cls, env: str, header: str = "X-API-Key") -> Auth:
        return cls(header=header, env=env)

    @classmethod
    def bearer(cls, env: str) -> Auth:
        return cls(header="Authorization", env=env, prefix="Bearer ")

    @classmethod
    def from_callable(
        cls, source: Callable[[], str | None], header: str = "Authorization", prefix: str = ""
    ) -> Auth:
        """Credentials from a callable (e.g. a token refresher), called on every request."""
        return cls(header=header, prefix=prefix, source=source)

    def headers(self) -> dict[str, str]:
        """The header to send. Raises ``PolicyRejected("auth")`` if the secret is missing."""
        value = self.source() if self.source is not None else os.environ.get(self.env or "")
        if not value or any(c in value for c in "\r\n"):
            raise PolicyRejected("auth")
        return {self.header: f"{self.prefix}{value}"}
