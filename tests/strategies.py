"""Shared complete mapping and typed model strategies."""

from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st

from researchgraph.model import (
    Actor,
    Approval,
    Artifact,
    Edge,
    Inheritance,
    Lease,
    Node,
    RuleViolation,
    Status,
    Write,
)

NOW = "2026-10-19T14:06:19Z"
LATER = "2026-10-19T14:07:19Z"
TEXT = st.text(st.characters(blacklist_categories=("Cs",)), max_size=50)
SCALARS = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), TEXT)
VALUES = st.recursive(
    SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(TEXT, children, max_size=4),
    ),
    max_leaves=15,
)
FIELDS = st.dictionaries(TEXT, VALUES, max_size=5)


@st.composite
def actors(draw):
    actor = {"kind": draw(st.sampled_from(["human", "agent"])), "name": draw(TEXT)}
    for key in ("email", "model", "version"):
        if draw(st.booleans()):
            actor[key] = draw(TEXT)
    return actor


@st.composite
def complete_nodes(draw):
    author = draw(actors())
    status = draw(
        st.sampled_from(["proposed", "active", "running", "done", "failed", "retired"])
    )
    approval = {
        "actor": draw(actors()),
        "at": NOW,
        "what": draw(st.sampled_from(["approve", "claim", "complete"])),
    }
    for key in ("role", "note"):
        if draw(st.booleans()):
            approval[key] = draw(TEXT)
    artifact = {
        "path": "artifacts/ta-abcdefgh/result.txt",
        "sha256": "a" * 64,
        "bytes": draw(st.integers(min_value=0)),
        "added_at": NOW,
        "added_by": draw(actors()),
    }
    dimensions = st.one_of(
        st.none(),
        st.fixed_dictionaries({"tokens": st.integers(min_value=0)}),
        st.fixed_dictionaries({"compute_seconds": st.integers(min_value=0)}),
        st.fixed_dictionaries(
            {
                "tokens": st.integers(min_value=0),
                "compute_seconds": st.integers(min_value=0),
            }
        ),
    )
    return {
        "id": "ta-abcdefgh",
        "type": "Task",
        "title": draw(
            st.text(
                st.characters(blacklist_categories=("Cs", "Cc", "Zl", "Zp")),
                max_size=50,
            )
        ),
        "body": draw(TEXT),
        "author": author,
        "created": NOW,
        "fields": draw(FIELDS),
        "status": status,
        "updated": NOW,
        "approvals": draw(st.lists(st.just(approval), max_size=3)),
        "artifacts": draw(st.lists(st.just(artifact), max_size=3)),
        "budget": draw(dimensions),
        "usage": draw(dimensions),
        "tags": sorted(draw(st.sets(TEXT, max_size=4))),
        "priority": draw(st.one_of(st.none(), st.integers())),
        "lease": {"actor": draw(actors()), "acquired_at": NOW, "expires_at": LATER}
        if status == "running"
        else None,
    }


@st.composite
def complete_edges(draw):
    retired = draw(st.booleans())
    return {
        "id": "ed-abcdefgh",
        "type": "tests",
        "from": "ta-abcdefgh",
        "to": "hy-abcdefgh",
        "author": draw(actors()),
        "created": NOW,
        "fields": draw(FIELDS),
        "retired": retired,
        "retired_at": NOW if retired else None,
    }


# Typed fields exclude non-finite floats so dataclass equality is meaningful.
FINITE_VALUES = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        TEXT,
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4), st.dictionaries(TEXT, children, max_size=4)
    ),
    max_leaves=15,
)
TYPED_FIELDS = st.dictionaries(TEXT, FINITE_VALUES, max_size=5)
TIMESTAMPS = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 30),
    timezones=st.just(UTC),
).map(lambda value: value.replace(microsecond=0))


def statuses():
    return st.sampled_from(list(Status))


def inheritances():
    return st.sampled_from(list(Inheritance))


def typed_actors():
    return actors().map(Actor.from_dict)


def approvals():
    return st.builds(
        Approval,
        actor=typed_actors(),
        at=TIMESTAMPS,
        what=st.sampled_from(
            [
                "approve",
                "reject",
                "claim",
                "complete",
                "renew",
                "release",
                "fail",
                "retire",
                "superseded",
                "budget",
            ]
        ),
        role=st.one_of(st.none(), TEXT),
        note=st.one_of(st.none(), TEXT),
    )


def artifacts():
    return st.builds(
        Artifact,
        path=st.just("artifacts/ta-abcdefgh/result.txt"),
        sha256=st.just("a" * 64),
        bytes=st.integers(min_value=0),
        added_at=TIMESTAMPS,
        added_by=typed_actors(),
    )


@st.composite
def leases(draw):
    acquired = draw(TIMESTAMPS)
    return Lease(
        actor=draw(typed_actors()),
        acquired_at=acquired,
        expires_at=acquired + timedelta(seconds=draw(st.integers(1, 86400))),
    )


@st.composite
def nodes(draw):
    mapping = draw(complete_nodes())
    mapping["fields"] = draw(TYPED_FIELDS)
    node = Node.from_dict(mapping)
    node.created = draw(TIMESTAMPS)
    node.updated = node.created + timedelta(seconds=draw(st.integers(0, 86400)))
    node.approvals = draw(st.lists(approvals(), max_size=3))
    node.artifacts = draw(st.lists(artifacts(), max_size=3))
    node.lease = draw(leases()) if node.status == Status.RUNNING else None
    return node


@st.composite
def edges(draw):
    mapping = draw(complete_edges())
    mapping["fields"] = draw(TYPED_FIELDS)
    edge = Edge.from_dict(mapping)
    edge.created = draw(TIMESTAMPS)
    edge.retired_at = edge.created if edge.retired else None
    return edge


def writes():
    return st.builds(
        Write,
        op=st.sampled_from(["propose", "link", "approve", "claim"]),
        actor=typed_actors(),
        role=st.one_of(st.none(), TEXT),
        now=TIMESTAMPS,
        args=TYPED_FIELDS,
        nodes=st.lists(nodes(), max_size=3),
        edges=st.lists(edges(), max_size=3),
    )


def rule_violations():
    return st.builds(
        RuleViolation, rule=TEXT, message=TEXT, ids=st.lists(TEXT, max_size=4)
    )
