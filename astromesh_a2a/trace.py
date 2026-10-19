"""Per-stage trace of a remote call, with secret redaction and W3C traceparent."""

from __future__ import annotations

import copy
import os
import re
import time
from typing import Any

_SECRET_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "x_api_key",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "secret",
        "password",
        "passwd",
        "token",
        "bearer",
        "cookie",
        "set_cookie",
        "session_token",
        "session_key",
        "session_cookie",
    }
)
_SECRET_SUFFIXES = (
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_api_key",
    "_apikey",
    "_secret_key",
    "_secret_access_key",
    "_private_key",
)
# Extra names that are secret only as URL query parameters (presigned URLs carry
# X-Amz-Signature / X-Amz-Credential and similar).
_QUERY_KEYS = frozenset({"key", "sig", "signature", "credential"})
_QUERY_SUFFIXES = ("_signature", "_credential")


def _is_secret_key(name: str, *, query: bool = False) -> bool:
    n = name.strip().lower().replace("-", "_")
    if n in _SECRET_KEYS or (query and (n in _QUERY_KEYS or n.endswith(_QUERY_SUFFIXES))):
        return True
    return n.endswith(_SECRET_SUFFIXES)


REDACTED = "[redacted]"
_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s?#]*@")
_QUERY = re.compile(r"([?&;])([\w.-]+)=([^&#\s]*)")
_SCHEME = re.compile(r"\b(Bearer|Basic)\s+[^\s,;]+", re.IGNORECASE)


def _query_sub(m: re.Match[str]) -> str:
    if _is_secret_key(m.group(2), query=True):
        return f"{m.group(1)}{m.group(2)}={REDACTED}"
    return m.group(0)


def _redact_str(s: str) -> str:
    s = _USERINFO.sub(r"\1[redacted]@", s)
    s = _QUERY.sub(_query_sub, s)
    return _SCHEME.sub(r"\1 [redacted]", s)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _is_secret_key(k) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _redact_str(value)
    return value


def _snapshot(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001
        return repr(value)


class Trace:
    """Ordered stage entries ``{stage, ms, ...fields}``; ms is time since the previous one."""

    def __init__(self) -> None:
        self.trace_id = os.urandom(16).hex()
        self._entries: list[dict[str, Any]] = []
        self._last = time.monotonic()

    def add(self, stage: str, /, **fields: Any) -> None:
        if "stage" in fields or "ms" in fields:
            raise ValueError("'stage' and 'ms' are reserved and cannot be used as fields")
        now = time.monotonic()
        entry = {"stage": stage, "ms": round((now - self._last) * 1000, 3)}
        entry.update({k: _snapshot(v) for k, v in fields.items()})
        self._entries.append(entry)
        self._last = now

    def to_dict(self) -> list[dict[str, Any]]:
        return [_redact(e) for e in self._entries]

    def traceparent(self) -> str:
        """W3C header: one trace id per Trace, a fresh span id per call."""
        return f"00-{self.trace_id}-{os.urandom(8).hex()}-01"
