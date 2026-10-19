"""Per-stage trace of a remote call, with secret redaction and W3C traceparent."""

from __future__ import annotations

import os
import re
import time
from typing import Any

_SECRET = re.compile(r"authorization|api[-_]?key|token|secret|password", re.IGNORECASE)
REDACTED = "[redacted]"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _SECRET.search(k) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class Trace:
    """Ordered stage entries ``{stage, ms, ...fields}``; ms is time since the previous one."""

    def __init__(self) -> None:
        self.trace_id = os.urandom(16).hex()
        self._entries: list[dict[str, Any]] = []
        self._last = time.monotonic()

    def add(self, stage: str, **fields: Any) -> None:
        now = time.monotonic()
        self._entries.append({"stage": stage, "ms": round((now - self._last) * 1000, 3), **fields})
        self._last = now

    def to_dict(self) -> list[dict[str, Any]]:
        return [_redact(e) for e in self._entries]

    def traceparent(self) -> str:
        """W3C header: one trace id per Trace, a fresh span id per call."""
        return f"00-{self.trace_id}-{os.urandom(8).hex()}-01"
