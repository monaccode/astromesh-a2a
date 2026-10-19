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
    """The remote task ended in a failed/rejected/canceled state."""


class A2ATimeout(A2AError):  # noqa: N818
    """A remote call or stream exceeded its time or event limit."""


class UnsupportedVersion(A2AError):  # noqa: N818
    """The remote speaks an A2A protocol version this library does not support."""


class ConfirmationRequired(A2AError):  # noqa: N818
    """The remote asked for a confirmation that only a human can give."""
