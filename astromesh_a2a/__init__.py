from typing import TYPE_CHECKING

from astromesh_a2a.card import CardSpec, RemoteCard, Skill
from astromesh_a2a.client import AgentClosedError, EventStream, LoopBindingError, RemoteAgent
from astromesh_a2a.errors import (
    A2AError,
    A2ATimeout,
    BlockedDestination,
    PolicyRejected,
    TaskFailed,
    UnsupportedVersion,
)
from astromesh_a2a.policy import Budget, BudgetMeter, Policy
from astromesh_a2a.trace import Trace
from astromesh_a2a.types import Auth, Event, Incoming, Origin, Outgoing, Result

if TYPE_CHECKING:  # the server needs the 'server' extra; type checkers still see it
    from astromesh_a2a.server import api_key_auth as api_key_auth
    from astromesh_a2a.server import create_app as create_app
    from astromesh_a2a.server import serve as serve

# Server names: NOT in __all__, so `from astromesh_a2a import *` works on a base install
# (without the 'server' extra). Imported on first attribute access.
_SERVER = ("api_key_auth", "create_app", "serve")


def __getattr__(name: str):
    # The server needs the 'server' extra (starlette, uvicorn): imported on first use.
    if name in _SERVER:
        from astromesh_a2a import server

        return getattr(server, name)
    raise AttributeError(f"module 'astromesh_a2a' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_SERVER})


__version__ = "0.1.0"

__all__ = [
    "A2AError",
    "A2ATimeout",
    "AgentClosedError",
    "Auth",
    "BlockedDestination",
    "Budget",
    "BudgetMeter",
    "CardSpec",
    "Event",
    "EventStream",
    "Incoming",
    "LoopBindingError",
    "Origin",
    "Outgoing",
    "Policy",
    "PolicyRejected",
    "RemoteAgent",
    "RemoteCard",
    "Result",
    "Skill",
    "TaskFailed",
    "Trace",
    "UnsupportedVersion",
]
