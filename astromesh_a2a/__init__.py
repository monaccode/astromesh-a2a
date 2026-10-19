from astromesh_a2a.card import CardSpec, RemoteCard, Skill
from astromesh_a2a.client import EventStream, RemoteAgent
from astromesh_a2a.errors import (
    A2AError,
    A2ATimeout,
    BlockedDestination,
    ConfirmationRequired,
    PolicyRejected,
    TaskFailed,
    UnsupportedVersion,
)
from astromesh_a2a.policy import Budget, BudgetMeter, Policy
from astromesh_a2a.trace import Trace
from astromesh_a2a.types import Auth, Event, Incoming, Origin, Outgoing, Result

_SERVER = ("create_app", "serve", "api_key_auth")


def __getattr__(name: str):
    # The server needs the 'server' extra (starlette, uvicorn): imported on first use.
    if name in _SERVER:
        from astromesh_a2a import server

        return getattr(server, name)
    raise AttributeError(f"module 'astromesh_a2a' has no attribute {name!r}")


__version__ = "0.1.0"

__all__ = [
    "A2AError",
    "A2ATimeout",
    "Auth",
    "BlockedDestination",
    "Budget",
    "BudgetMeter",
    "CardSpec",
    "ConfirmationRequired",
    "Event",
    "EventStream",
    "Incoming",
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
    "api_key_auth",
    "create_app",
    "serve",
]
