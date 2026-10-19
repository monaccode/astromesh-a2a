"""Exceptions raised by astromesh-a2a."""


class A2AError(Exception):
    """Base class for every error this library raises."""


class BlockedDestination(A2AError):  # noqa: N818
    """An outbound request was refused by the egress guard (SSRF, size, redirect)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
