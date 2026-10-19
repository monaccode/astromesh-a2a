"""Agent Card values: what our server publishes (``CardSpec``) and what a remote shows us
(``RemoteCard``). Plain dataclasses; protobuf is converted here, at the edge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import TransportProtocol

RPC_PATH = "/a2a"
PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True)
class Skill:
    """One skill listed on an Agent Card."""

    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ("text/plain",)
    output_modes: tuple[str, ...] = ("text/plain",)

    def __post_init__(self) -> None:
        for name in ("id", "name", "description"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"Skill.{name} must be a non-empty str")
        for name in ("tags", "examples", "input_modes", "output_modes"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    def to_proto(self) -> AgentSkill:
        return AgentSkill(
            id=self.id,
            name=self.name,
            description=self.description,
            tags=list(self.tags),
            examples=list(self.examples),
            input_modes=list(self.input_modes),
            output_modes=list(self.output_modes),
        )

    @classmethod
    def from_proto(cls, skill: AgentSkill) -> Skill:
        return cls(
            id=skill.id or "(unnamed)",
            name=skill.name or skill.id or "(unnamed)",
            description=skill.description or "(no description)",
            tags=tuple(skill.tags),
            examples=tuple(skill.examples),
            input_modes=tuple(skill.input_modes),
            output_modes=tuple(skill.output_modes),
        )


@dataclass
class CardSpec:
    """What ``create_app``/``serve`` publish at ``/.well-known/agent-card.json``.

    ``url`` is the PUBLIC base URL the app is reachable at (where it is mounted); the
    JSON-RPC 1.0 interface is announced at ``<url>/a2a``.
    """

    name: str
    description: str
    version: str
    url: str
    skills: list[Skill] = field(default_factory=list)
    streaming: bool = True

    def __post_init__(self) -> None:
        for name in ("name", "description", "version", "url"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"CardSpec.{name} must be a non-empty str")
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("CardSpec.url must be an absolute http(s) URL")
        if parts.query or parts.fragment:
            raise ValueError("CardSpec.url must not have a query or fragment")
        if not all(isinstance(s, Skill) for s in self.skills):
            raise TypeError("CardSpec.skills must be Skill instances")

    @property
    def interface_url(self) -> str:
        return self.url.rstrip("/") + RPC_PATH

    def to_proto(self) -> AgentCard:
        return AgentCard(
            name=self.name,
            description=self.description,
            version=self.version,
            supported_interfaces=[
                AgentInterface(
                    url=self.interface_url,
                    protocol_binding=TransportProtocol.JSONRPC,
                    protocol_version=PROTOCOL_VERSION,
                )
            ],
            capabilities=AgentCapabilities(streaming=self.streaming),
            default_input_modes=["text/plain", "application/json"],
            default_output_modes=["text/plain", "application/json"],
            skills=[s.to_proto() for s in self.skills],
        )


@dataclass(frozen=True)
class RemoteCard:
    """A remote agent's card, as vetted by ``RemoteAgent.card()``.

    ``interface_url``/``protocol_version`` are those of the ONE interface the client will
    use. Every text field was written by the remote: treat it as untrusted. ``raw`` is a
    copy of the vetted SDK ``AgentCard`` (protobuf) for anything not surfaced here.

    ``has_signatures`` only says the card CARRIES signatures (anyone can attach one).
    ``signature_verified`` is ``True`` only when the policy required a signature and it
    verified against a pinned key; trust that one.
    """

    name: str
    description: str
    version: str
    interface_url: str
    protocol_version: str
    streaming: bool
    has_signatures: bool
    signature_verified: bool
    skills: tuple[Skill, ...] = ()
    raw: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_proto(cls, card: AgentCard, *, signature_verified: bool = False) -> RemoteCard:
        raw = AgentCard()
        raw.CopyFrom(card)
        interface = card.supported_interfaces[0] if card.supported_interfaces else None
        return cls(
            name=card.name,
            description=card.description,
            version=card.version,
            interface_url=interface.url if interface else "",
            protocol_version=interface.protocol_version if interface else "",
            streaming=bool(card.capabilities.streaming),
            has_signatures=bool(card.signatures),
            signature_verified=signature_verified,
            skills=tuple(Skill.from_proto(s) for s in card.skills),
            raw=raw,
        )
