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
    """Stored node lifecycle values, not derived readiness or blocking states."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    RETIRED = "retired"


class Inheritance(StrEnum):
    """Edge inheritance modes on supersede: both directions, outgoing, or none."""

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
    """A refusal carrying a stable rule, message, and copied affected-id list.

    ``ids=None`` creates an empty list; ``message`` is also the exception text.
    """

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
    """Return an aware UTC datetime from whole-second RFC 3339 text.

    Require a ``Z`` or numeric offset; reject fractions, invalid dates, and
    invalid offsets with ValueError. No graph timestamp policy is checked.
    """
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be whole-second RFC 3339 with an offset")
    # datetime accepts offsets >= 24 hours only by raising ValueError, but also
    # normalises offset minutes >= 60; RFC 3339 does not permit those minutes.
    if not value.endswith("Z") and int(value[-2:]) > 59:
        raise ValueError("Timestamp offset minutes must be less than 60")
    return _utc(datetime.fromisoformat(value))


def format_timestamp(value: datetime) -> str:
    """Return canonical UTC RFC 3339 text ending in ``Z``.

    Convert an aware datetime's offset to UTC; raise ValueError for naive
    datetimes or nonzero microseconds rather than rounding them.
    """
    return _utc(value).isoformat().replace("+00:00", "Z")


def new_id(prefix: str, exists_callback: Callable[[str], bool]) -> str:
    """Return ``prefix-`` plus eight random lowercase base32 characters.

    Require two to four lowercase ASCII prefix letters or raise ValueError.
    Retry while ``exists_callback(candidate)`` is truthy; this does not reserve
    the identifier, and callback errors propagate.
    """
    if re.fullmatch(r"[a-z]{2,4}", prefix) is None:
        raise ValueError("Identifier prefix must be 2–4 lowercase letters")
    while True:
        candidate = prefix + "-" + "".join(secrets.choice(_BASE32) for _ in range(8))
        if not exists_callback(candidate):
            return candidate


@dataclass(frozen=True)
class Actor:
    """Frozen declared identity with required ``kind`` and ``name`` strings.

    Optional email, model, and version default to None and do not participate
    in equality or hashing: identity is the kind/name pair. Construction does
    not validate kind values or authenticate the actor; roles live on writes
    and approvals, not on this record.
    """

    kind: str
    name: str
    email: str | None = field(default=None, compare=False)
    model: str | None = field(default=None, compare=False)
    version: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        """Return kind/name and optional email/model/version, omitting None values."""
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
        """Read required kind/name and optional identity metadata without validation.

        Missing optional keys become None; unknown keys are ignored. Values
        are passed through, not copied, and missing required keys raise KeyError.
        """
        return cls(
            value["kind"],
            value["name"],
            value.get("email"),
            value.get("model"),
            value.get("version"),
        )


@dataclass
class Approval:
    """Event record containing actor, timestamp ``at``, and operation ``what``.

    The per-call declared role and explanatory note both default to None.
    Construction does not enforce approval permissions or event vocabulary.
    """

    actor: Actor
    at: datetime
    what: str
    role: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        """Return actor/at/what with UTC time and omit None role and note values.

        Actor serialization creates a mapping; timestamp formatting can raise
        ValueError for a naive or subsecond ``at``.
        """
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
        """Read actor/at/what, parsing UTC time, with optional role and note.

        Ignore extra keys; missing required keys raise KeyError and malformed
        timestamps raise ValueError. No permission or operation check is made.
        """
        return cls(
            actor=Actor.from_dict(value["actor"]),
            at=parse_timestamp(value["at"]),
            what=value["what"],
            role=value.get("role"),
            note=value.get("note"),
        )


@dataclass
class Artifact:
    """Artifact metadata: path, sha256, byte count, added_at, and added_by.

    Every field is required. This record neither reads the artifact nor checks
    its path, digest, size, or attribution; write validators enforce policy.
    """

    path: str
    sha256: str
    bytes: int
    added_at: datetime
    added_by: Actor

    def to_dict(self) -> dict:
        """Return all five metadata keys with UTC added_at and an actor mapping.

        Timestamp formatting raises ValueError for naive or subsecond times;
        no artifact bytes are read or hashed.
        """
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "added_at": format_timestamp(self.added_at),
            "added_by": self.added_by.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Artifact":
        """Read required metadata keys, parsing added_at and rebuilding added_by.

        Extra keys are ignored; missing keys raise KeyError and invalid time
        text raises ValueError. Path, digest, and byte count pass through.
        """
        return cls(
            path=value["path"],
            sha256=value["sha256"],
            bytes=value["bytes"],
            added_at=parse_timestamp(value["added_at"]),
            added_by=Actor.from_dict(value["added_by"]),
        )


@dataclass
class Lease:
    """Required holder actor, acquired_at, and expires_at for a work lease.

    Construction does not check timestamp ordering, duration, or node status.
    """

    actor: Actor
    acquired_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        """Return actor, acquired_at, and expires_at with canonical UTC times.

        Naive or subsecond timestamps raise ValueError during formatting.
        """
        return {
            "actor": self.actor.to_dict(),
            "acquired_at": format_timestamp(self.acquired_at),
            "expires_at": format_timestamp(self.expires_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> "Lease":
        """Rebuild the required actor and parse both required lease timestamps.

        Ignore extra keys; missing keys raise KeyError and invalid timestamps
        raise ValueError. Expiry ordering is not validated here.
        """
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
    """Graph node record; policy validation is separate from construction.

    Required fields are id, type, title, body, author, and created. Fields is a
    fresh mapping; status defaults to PROPOSED, and updated defaults to created
    when None. Approvals, artifacts, and tags are fresh lists. Budget and usage
    are optional token/compute_seconds mappings; priority and lease also default
    to None. Graph immutability and lifecycle rules are not enforced here.
    """

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
        """Return every node key, including None-valued optional fields.

        Deep-copy fields, copy tags, serialize nested records and UTC times,
        and stringify status. Budget/usage retain only non-None tokens and
        compute_seconds, becoming None if empty; their values are not deep-copied.
        Invalid naive or subsecond timestamps raise ValueError.
        """
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
        """Rebuild a node from required identity/content/author/created keys.

        Parse timestamps and Status, deep-copy fields, rebuild nested records,
        and copy tags. Missing status becomes PROPOSED; absent or null updated
        becomes created. Missing fields/lists use empty containers; absent
        budget, usage, priority, or lease becomes None. Budget/usage keep only
        non-null tokens/compute_seconds values without deep-copying them.
        Extra keys are ignored. Missing required keys raise KeyError; invalid
        timestamps or status values raise ValueError. No graph policy is checked.
        """
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
    """Directed graph edge with required id, type, from_, to, author, and created.

    Fields defaults to a fresh mapping; retired defaults to False and retired_at
    to None. ``from_`` maps to the stored ``from`` key. Construction does not
    check endpoints, inheritance, retirement consistency, or graph invariants.
    """

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
        """Return all edge keys, using ``from`` for from_ and copying fields deeply.

        Serialize author and UTC times; retain retired_at=None. Naive or
        subsecond timestamps raise ValueError during formatting.
        """
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
        """Rebuild required edge identity/endpoints/author/created from a mapping.

        Deep-copy fields (empty when absent); default retired to False and
        absent or null retired_at to None. Ignore extra keys. Missing required
        keys raise KeyError and malformed timestamps raise ValueError; endpoint
        and retirement policy is not validated.
        """
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
    """Validator-facing write proposal with operation, actor, role, time, and args.

    All constructor arguments are required; role may be None. Nodes and edges
    contain new or changed records as they will be after the write, not the
    complete graph. Construction does not execute or validate the proposal.
    """

    op: str
    actor: Actor
    role: str | None
    now: datetime
    args: dict[str, Any]
    nodes: list[Node]
    edges: list[Edge]

    def to_dict(self) -> dict:
        """Return op/actor/role/now/args/nodes/edges, retaining a None role.

        Deep-copy args, serialize each graph record, and format now in UTC.
        Invalid naive or subsecond record timestamps raise ValueError.
        """
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
        """Rebuild a write from required op/actor/now/args/nodes/edges keys.

        Missing role becomes None; args is deep-copied and nested records are
        rebuilt by their conversion helpers. Ignore extra keys. Missing required
        keys raise KeyError; invalid timestamps or node statuses raise ValueError.
        No operation is executed and no write policy is checked.
        """
        return cls(
            op=value["op"],
            actor=Actor.from_dict(value["actor"]),
            role=value.get("role"),
            now=parse_timestamp(value["now"]),
            args=deepcopy(value["args"]),
            nodes=[Node.from_dict(item) for item in value["nodes"]],
            edges=[Edge.from_dict(item) for item in value["edges"]],
        )
