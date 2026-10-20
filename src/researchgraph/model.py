"""Typed graph data contracts; graph and write policy belongs to validators."""

import re
import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    RETIRED = "retired"


class Inheritance(StrEnum):
    BOTH = "both"
    OUTGOING = "outgoing"
    NONE = "none"


NODE_IMMUTABLE_KEYS = frozenset(
    {"id", "type", "title", "body", "author", "created", "fields"}
)
EDGE_IMMUTABLE_KEYS = frozenset(
    {"id", "type", "from", "to", "author", "created", "fields"}
)
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", re.ASCII
)
_BASE32 = "abcdefghijklmnopqrstuvwxyz234567"


class RuleViolation(Exception):
    """A refusal with a stable rule name and the affected graph identifiers."""

    def __init__(self, rule: str, message: str, ids: list[str] | None = None):
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.ids = list(ids) if ids is not None else []


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    result = value.astimezone(UTC)
    if result.microsecond:
        raise ValueError("Timestamp must have whole-second precision")
    return result


def parse_timestamp(value: str) -> datetime:
    """Parse whole-second RFC 3339 with an explicit offset, normalising to UTC."""
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be whole-second RFC 3339 with an offset")
    # datetime accepts offsets >= 24 hours only by raising ValueError, but also
    # normalises offset minutes >= 60; RFC 3339 does not permit those minutes.
    if not value.endswith("Z") and int(value[-2:]) > 59:
        raise ValueError("Timestamp offset minutes must be less than 60")
    return _utc(datetime.fromisoformat(value))


def format_timestamp(value: datetime) -> str:
    """Format an aware, whole-second datetime as canonical UTC with a Z suffix."""
    return _utc(value).isoformat().replace("+00:00", "Z")


def new_id(prefix: str, exists_callback: Callable[[str], bool]) -> str:
    """Generate a random base32 identifier, retrying existing identifiers."""
    if re.fullmatch(r"[a-z]{2,4}", prefix) is None:
        raise ValueError("Identifier prefix must be 2–4 lowercase letters")
    while True:
        candidate = prefix + "-" + "".join(secrets.choice(_BASE32) for _ in range(8))
        if not exists_callback(candidate):
            return candidate


@dataclass(frozen=True)
class Actor:
    kind: str
    name: str
    email: str | None = field(default=None, compare=False)
    model: str | None = field(default=None, compare=False)
    version: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {
            key: value
            for key, value in (
                ("kind", self.kind),
                ("name", self.name),
                ("email", self.email),
                ("model", self.model),
                ("version", self.version),
            )
            if value is not None
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Actor":
        return cls(
            value["kind"],
            value["name"],
            value.get("email"),
            value.get("model"),
            value.get("version"),
        )


@dataclass
class Approval:
    actor: Actor
    at: datetime
    what: str
    role: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        result = {"actor": self.actor.to_dict()}
        if self.role is not None:
            result["role"] = self.role
        result["at"] = format_timestamp(self.at)
        result["what"] = self.what
        if self.note is not None:
            result["note"] = self.note
        return result

    @classmethod
    def from_dict(cls, value: Mapping) -> "Approval":
        return cls(
            actor=Actor.from_dict(value["actor"]),
            at=parse_timestamp(value["at"]),
            what=value["what"],
            role=value.get("role"),
            note=value.get("note"),
        )


@dataclass
class Artifact:
    path: str
    sha256: str
    bytes: int
    added_at: datetime
    added_by: Actor

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "added_at": format_timestamp(self.added_at),
            "added_by": self.added_by.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Artifact":
        return cls(
            path=value["path"],
            sha256=value["sha256"],
            bytes=value["bytes"],
            added_at=parse_timestamp(value["added_at"]),
            added_by=Actor.from_dict(value["added_by"]),
        )


@dataclass
class Lease:
    actor: Actor
    acquired_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        return {
            "actor": self.actor.to_dict(),
            "acquired_at": format_timestamp(self.acquired_at),
            "expires_at": format_timestamp(self.expires_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Lease":
        return cls(
            actor=Actor.from_dict(value["actor"]),
            acquired_at=parse_timestamp(value["acquired_at"]),
            expires_at=parse_timestamp(value["expires_at"]),
        )


def _dimensions(value: Mapping | None) -> dict | None:
    if value is None:
        return None
    return {
        key: value[key]
        for key in ("tokens", "compute_seconds")
        if value.get(key) is not None
    } or None


@dataclass(slots=True, weakref_slot=True)
class Node:
    id: str
    type: str
    title: str
    body: str
    author: Actor
    created: datetime
    fields: dict[str, Any] = field(default_factory=dict)
    status: Status = Status.PROPOSED
    updated: datetime | None = None
    approvals: list[Approval] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    budget: dict[str, int] | None = None
    usage: dict[str, int] | None = None
    tags: list[str] = field(default_factory=list)
    priority: int | None = None
    lease: Lease | None = None

    def __post_init__(self):
        if self.updated is None:
            self.updated = self.created

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "author": self.author.to_dict(),
            "created": format_timestamp(self.created),
            "fields": deepcopy(self.fields),
            "status": str(self.status),
            "updated": format_timestamp(self.updated),
            "approvals": [approval.to_dict() for approval in self.approvals],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "budget": _dimensions(self.budget),
            "usage": _dimensions(self.usage),
            "tags": list(self.tags),
            "priority": self.priority,
            "lease": self.lease.to_dict() if self.lease is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Node":
        created = parse_timestamp(value["created"])
        return cls(
            id=value["id"],
            type=value["type"],
            title=value["title"],
            body=value["body"],
            author=Actor.from_dict(value["author"]),
            created=created,
            fields=deepcopy(value.get("fields", {})),
            status=Status(value.get("status", Status.PROPOSED)),
            updated=parse_timestamp(value["updated"])
            if value.get("updated") is not None
            else created,
            approvals=[Approval.from_dict(item) for item in value.get("approvals", [])],
            artifacts=[Artifact.from_dict(item) for item in value.get("artifacts", [])],
            budget=_dimensions(value.get("budget")),
            usage=_dimensions(value.get("usage")),
            tags=list(value.get("tags", [])),
            priority=value.get("priority"),
            lease=Lease.from_dict(value["lease"])
            if value.get("lease") is not None
            else None,
        )


@dataclass
class Edge:
    id: str
    type: str
    from_: str
    to: str
    author: Actor
    created: datetime
    fields: dict[str, Any] = field(default_factory=dict)
    retired: bool = False
    retired_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "from": self.from_,
            "to": self.to,
            "author": self.author.to_dict(),
            "created": format_timestamp(self.created),
            "fields": deepcopy(self.fields),
            "retired": self.retired,
            "retired_at": format_timestamp(self.retired_at)
            if self.retired_at is not None
            else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Edge":
        return cls(
            id=value["id"],
            type=value["type"],
            from_=value["from"],
            to=value["to"],
            author=Actor.from_dict(value["author"]),
            created=parse_timestamp(value["created"]),
            fields=deepcopy(value.get("fields", {})),
            retired=value.get("retired", False),
            retired_at=parse_timestamp(value["retired_at"])
            if value.get("retired_at") is not None
            else None,
        )


@dataclass
class Write:
    op: str
    actor: Actor
    role: str | None
    now: datetime
    args: dict[str, Any]
    nodes: list[Node]
    edges: list[Edge]

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "actor": self.actor.to_dict(),
            "role": self.role,
            "now": format_timestamp(self.now),
            "args": deepcopy(self.args),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Write":
        return cls(
            op=value["op"],
            actor=Actor.from_dict(value["actor"]),
            role=value.get("role"),
            now=parse_timestamp(value["now"]),
            args=deepcopy(value["args"]),
            nodes=[Node.from_dict(item) for item in value["nodes"]],
            edges=[Edge.from_dict(item) for item in value["edges"]],
        )
