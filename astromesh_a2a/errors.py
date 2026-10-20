"""Exceptions raised by astromesh-a2a."""


class A2AError(Exception):
    """Base class for every error this library raises."""


class BlockedDestination(A2AError):  # noqa: N818
    """An outbound request was refused by the egress guard (SSRF, size, redirect)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PolicyRejected(A2AError):  # noqa: N818
    """The configured Policy refused the operation (host, part kind, size, budget...)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class TaskFailed(A2AError):  # noqa: N818
    """The remote task failed, or the call to it failed in a way we could not classify.

    ``remote`` is ``True`` only when the remote itself reported the task as ended
    (failed, rejected, canceled, not found, not cancelable): the task is over. ``False`` for an
    unclassified failure (an HTTP error, an unexpected SDK exception...), whose message is
    generic and never quotes the remote: the task may still be alive.
    """

    def __init__(self, message: str, *, remote: bool = False):
        super().__init__(message)
        self.remote = remote


class A2ATimeout(A2AError):  # noqa: N818
    """A remote call or stream exceeded its time or event limit."""


class UnsupportedVersion(A2AError):  # noqa: N818
    """The remote speaks an A2A protocol version this library does not support."""
