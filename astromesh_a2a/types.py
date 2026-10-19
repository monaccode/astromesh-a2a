"""Our public value types (no protobuf crosses this boundary)."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
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


@dataclass(frozen=True)
class Incoming:
    """What a served agent receives for one A2A message.

    ``origin`` is ALWAYS ``Origin.AGENT`` and ``untrusted`` always ``True``: the sender is
    another program, whatever it claims. ``claimed_origin`` is the ``origin`` the remote
    wrote in its message metadata (e.g. ``"human"``), kept for logging only; never use it
    to decide anything. A reply to a task that asked for confirmation arrives the same way
    (``task_id`` set): a remote "yes" is not a human confirmation, and this library never
    marks anything as confirmed.
    """

    text: str
    data: list[dict[str, Any]]
    origin: Origin
    context_id: str
    untrusted: bool = True
    task_id: str | None = None
    claimed_origin: str | None = None
    caller: str | None = None


@dataclass(frozen=True)
class Outgoing:
    """What a served agent answers.

    ``answer`` (text) and ``data`` (JSON objects) complete the task. With
    ``needs_confirmation`` set the task goes to ``input_required`` with that text instead
    (``answer``/``data`` are not sent); the remote's next message on the task reaches the
    handler again as an untrusted ``Incoming``.
    """

    answer: str = ""
    data: Sequence[dict[str, Any]] = ()
    needs_confirmation: str | None = None
