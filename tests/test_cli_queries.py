"""Read CLI contracts over operation-built graphs and the installed console."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from researchgraph.model import Actor, format_timestamp
from researchgraph.project import Project
from tests.helpers import ALICE, NOW, git, repository
from tests.test_cli import invoke, refused, run_console, succeeds

pytestmark = pytest.mark.cli

READ_NOW = NOW + timedelta(hours=1)
QUERY_ARGUMENTS = {
    "ready": None,
    "blocked": None,
    "queue": None,
    "stale": None,
    "digest": None,
    "show": "running",
    "history": "old_result",
    "evidence": "claim",
    "weakest": "claim",
    "alternatives": "hypothesis",
    "predictions": "hypothesis",
    "matrix": "question",
    "notes": "hypothesis",
    "subtree": "parent",
    "since": "since",
    "where": None,
}
ID_QUERIES = [
    name
    for name, argument in QUERY_ARGUMENTS.items()
    if argument is not None and name != "since"
]


class Graph:
    """Build only through committed public operations, with distinct clocks."""

    def __init__(self, path):
        self.root = repository(path)
        self.project = Project.init(self.root, actor=ALICE, now=NOW)
        self.seconds = 0
        self.ids = {}

    def write(self, operation, *args, **kwargs):
        self.seconds += 2
        context = {
            "actor": ALICE,
            "now": NOW + timedelta(seconds=self.seconds),
        }
        context.update(kwargs)
        try:
            return getattr(self.project, operation)(*args, **context)
        finally:
            self.project.index.close()

    def node(self, key, kind, title=None, **kwargs):
        value = self.write("propose", kind, title or key, **kwargs)
        self.ids[key] = value["id"]
        return value["id"]

    def edge(self, kind, source, target, **kwargs):
        return self.write("link", kind, source, target, **kwargs)

    def close(self):
        self.project.close()


def source_snapshot(root):
    paths = [root / "graph", root / "artifacts"]
    files = [p for directory in paths for p in directory.rglob("*") if p.is_file()]
    files += [root / ".researchgraph/profile.yaml"]
    history = root / ".researchgraph/history.jsonl"
    if history.exists():
        files.append(history)
    return (
        {p.relative_to(root).as_posix(): p.read_bytes() for p in files},
        git(root, "rev-parse", "HEAD"),
        git(root, "status", "--porcelain"),
    )


def prepare_cache(root, deleted):
    with Project.open(root) as project:
        project.ready(actor=ALICE, now=READ_NOW)
        cache = project.index.path
    assert cache.is_file()
    if deleted:
        cache.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(cache) + suffix).unlink(missing_ok=True)
        assert not cache.exists()


def read_cli(root, *arguments, deleted=False, **kwargs):
    prepare_cache(root, deleted)
    before = source_snapshot(root)
    result = invoke(root, *arguments, now=READ_NOW, **kwargs)
    assert source_snapshot(root) == before
    return result


def api(root, name, *args, now=READ_NOW, **kwargs):
    with Project.open(root, read_only=True) as project:
        return getattr(project, name)(*args, actor=ALICE, now=now, **kwargs)


def ids(rows):
    return [row["id"] for row in rows]


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    g = Graph(tmp_path_factory.mktemp("cli-queries") / "project")
    h = g.node("hypothesis", "Hypothesis")
    g.ids["before_approval"] = git(g.root, "rev-parse", "HEAD")
    g.ids["between"] = format_timestamp(NOW + timedelta(seconds=g.seconds + 1))
    g.write("approve", h, role="human")
    g.ids["after_approval"] = git(g.root, "rev-parse", "HEAD")
    q = g.node("question", "Question")
    q2 = g.node("question2", "Question")
    h2 = g.node("alternative", "Hypothesis")
    outside = g.node("outside", "Hypothesis")
    for hypothesis, question in [(h, q), (h2, q), (h, q2), (outside, q2)]:
        g.edge("answers", hypothesis, question)
    removed = g.node("removed_alternative", "Hypothesis")
    g.edge("answers", removed, q)
    g.write("retire", removed, "not an alternative")
    pending = g.node("pending", "Hypothesis")
    g.node("pending_later", "Prediction")
    p = g.node("prediction", "Prediction")
    single = g.node("single", "Task")
    equal = g.node("equal", "Prediction")
    for source, target, expected in [
        (p, h, "yes"),
        (p, h2, "yes"),
        (p, outside, "no"),
        (single, h, "yes"),
        (equal, h, "same"),
        (equal, h2, "same"),
    ]:
        g.edge("tests", source, target, fields={"expected": expected})
    removed_test = g.node("removed_test", "Task")
    g.edge("tests", removed_test, h, fields={"expected": "obsolete"})
    g.write("retire", removed_test, "not a live prediction")
    old = g.node("old_result", "Result", fields={"runs": 9})
    artifact = g.root / f"artifacts/{old}/result.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("measured result", encoding="utf-8")
    g.write("attach", old, artifacts=[artifact.relative_to(g.root).as_posix()])
    g.edge("pins", h, old, fields={"artifact": artifact.relative_to(g.root).as_posix()})
    input_ = g.node("input", "Reference", fields={"doi": "example"})
    g.edge("derived-from", old, input_)
    c = g.node("claim", "Claim")
    g.edge("cites", c, old)
    for key, fields in [
        ("one", {"runs": 1}),
        ("one_tie", {"runs": 1, "held_out": False}),
        ("held", {"runs": 1, "held_out": True}),
        ("many", {"runs": 3}),
    ]:
        result = g.node(key, "Result", fields=fields)
        g.edge("cites", c, result)
    g.write("approve", c, role="human")
    replacement = g.write("supersede", old, inherit_edges=False)
    g.ids["replacement"] = replacement["id"]
    g.edge("derived-from", g.ids["many"], input_)
    g.edge("supports", c, h2)
    g.edge("contradicts", c, outside)
    first_note = g.node("note", "Note", body="first note")
    g.edge("about", first_note, h)
    last_note = g.node("last_note", "Note", body="latest note")
    g.edge("about", last_note, h)
    g.write("instruct", h, "inspect the evidence")
    parent = g.node("parent", "Task")
    child = g.node("child", "Task")
    g.edge("part-of", child, parent)
    g.write("claim", child)
    g.write("complete", child, "finished", usage={"tokens": 7})
    blocker = g.node("blocker", "Task")
    blocked = g.node("blocked", "Task")
    g.ids["blocking_edge"] = g.edge("blocks", blocker, blocked)["id"]
    running = g.node("running", "Task")
    g.write("claim", running, lease_seconds=1)
    leased = g.node("leased", "Task")
    g.write("claim", leased, lease_seconds=7200)
    failed = g.node("failed", "Task")
    g.write("claim", failed)
    g.write("fail", failed, "fit failed", error="overflow")
    retired = g.node("retired", "Task")
    g.write("retire", retired, "obsolete")
    target = g.node(
        "filter",
        "Task",
        "FIT expansion",
        body="careful fit",
        fields={"kind": "expansion", "command": "a=b"},
        actor=Actor("agent", "agent1"),
    )
    g.write("tag", target, add=["x"])
    g.write("budget", target, tokens=1)
    g.write("claim", target)
    g.write("release", target, usage={"tokens": 2})
    g.node("wrong_tag", "Task", "fit", fields={"kind": "expansion"})
    g.node("wrong_text", "Task", "unrelated", fields={"kind": "expansion"})
    g.ids["since"] = format_timestamp(NOW)
    g.ids["cutoff"] = format_timestamp(NOW + timedelta(seconds=g.seconds))
    assert pending != h
    g.close()
    return g


@pytest.mark.parametrize("name", QUERY_ARGUMENTS)
@pytest.mark.parametrize("boundary", [None, "timestamp", "commit"])
def test_all_query_json_matches_public_api_and_cache(graph, name, boundary):
    key = QUERY_ARGUMENTS[name]
    arguments = () if key is None else (graph.ids[key],)
    as_of = {None: None, "timestamp": graph.ids["cutoff"], "commit": "HEAD"}[boundary]
    expected = api(graph.root, name, *arguments, as_of=as_of)
    tail = () if as_of is None else ("--as-of", as_of)
    outputs = []
    for deleted in (False, True):
        outputs.append(
            succeeds(
                read_cli(
                    graph.root,
                    name,
                    *arguments,
                    *tail,
                    "--read-only",
                    deleted=deleted,
                    agent="reader:model:v1",
                    role="owner",
                )
            )
        )
    assert outputs == [expected, expected]


@pytest.mark.parametrize("name", QUERY_ARGUMENTS)
@pytest.mark.parametrize("deleted", [False, True])
def test_query_common_options_before_and_after_command(graph, name, deleted):
    key = QUERY_ARGUMENTS[name]
    arguments = () if key is None else (graph.ids[key],)
    expected = api(graph.root, name, *arguments)
    before = succeeds(
        read_cli(
            graph.root,
            "--read-only",
            name,
            *arguments,
            deleted=deleted,
            agent="reader:model:v1",
            role="owner",
        )
    )
    after = succeeds(
        read_cli(
            graph.root,
            name,
            *arguments,
            "--project",
            str(graph.root),
            "--json",
            "--now",
            format_timestamp(READ_NOW),
            "--as-agent",
            "reader:model:v1",
            "--role",
            "owner",
            "--read-only",
            "--no-commit",
            deleted=deleted,
            agent=":invalid",
            role="invalid",
        )
    )
    assert before == after == expected


@pytest.mark.parametrize("deleted", [False, True])
def test_ready_blocked_queue_and_expired_show(graph, deleted):
    ready = succeeds(read_cli(graph.root, "ready", deleted=deleted))["items"]
    present = set(ids(ready))
    assert {graph.ids[k] for k in ("running", "blocker")} <= present
    assert not present & {
        graph.ids[k]
        for k in (
            "pending",
            "child",
            "failed",
            "retired",
            "leased",
            "blocked",
            "note",
            "filter",
        )
    }
    blocked = succeeds(read_cli(graph.root, "blocked", deleted=deleted))["items"]
    assert [(row["id"], row["blocking"]) for row in blocked] == [
        (graph.ids["blocked"], [graph.ids["blocker"]]),
    ]
    queue = succeeds(read_cli(graph.root, "queue", deleted=deleted))["items"]
    assert graph.ids["pending"] in ids(queue)
    assert graph.ids["note"] not in ids(queue)
    assert queue == sorted(queue, key=lambda row: (row["created"], row["id"]))
    assert all(row["gate"]["roles"] == ["human"] for row in queue)
    shown = succeeds(
        read_cli(graph.root, "show", graph.ids["running"], deleted=deleted)
    )
    assert shown["status"] == "running"
    assert shown["flags"]["lease_expired"] and shown["flags"]["ready"]
    edge = succeeds(
        read_cli(graph.root, "show", graph.ids["blocking_edge"], deleted=deleted)
    )
    assert edge == api(graph.root, "show", graph.ids["blocking_edge"])
    assert edge["from"] == graph.ids["blocker"]


@pytest.mark.parametrize("deleted", [False, True])
def test_evidence_weakest_history_notes_subtree_and_digest(graph, deleted):
    evidence = succeeds(
        read_cli(graph.root, "evidence", graph.ids["claim"], deleted=deleted)
    )
    assert set(ids(evidence["items"])) == {
        graph.ids[k]
        for k in (
            "old_result",
            "input",
            "one",
            "one_tie",
            "held",
            "many",
        )
    }
    assert next(
        row for row in evidence["items"] if row["id"] == graph.ids["old_result"]
    )["artifacts"]
    ranked = succeeds(
        read_cli(graph.root, "weakest", graph.ids["claim"], deleted=deleted)
    )
    assert ids(ranked) == (
        [graph.ids["old_result"]]
        + sorted([graph.ids["one"], graph.ids["one_tie"]])
        + [graph.ids["held"], graph.ids["many"]]
    )
    history = succeeds(
        read_cli(graph.root, "history", graph.ids["old_result"], deleted=deleted)
    )
    assert {row["kind"] for row in history} >= {
        "supersedes",
        "approval",
        "interval",
        "artifact",
    }
    assert all(row["commit"] for row in history)
    assert {row["node"] for row in history} >= {
        graph.ids["old_result"],
        graph.ids["replacement"],
    }
    notes = succeeds(
        read_cli(graph.root, "notes", graph.ids["hypothesis"], deleted=deleted)
    )
    assert ids(notes) == [graph.ids["last_note"], graph.ids["note"]]
    tree = succeeds(
        read_cli(graph.root, "subtree", graph.ids["parent"], deleted=deleted)
    )
    assert set(ids(tree["nodes"])) == {graph.ids["parent"], graph.ids["child"]}
    assert tree["status_counts"] == {"active": 1, "done": 1}
    assert tree["usage"] == {"tokens": 7, "compute_seconds": 0}
    digest = succeeds(read_cli(graph.root, "digest", deleted=deleted))
    failed = next(row for row in digest["items"] if row["kind"] == "failed_24h")
    assert failed["count"] == 1 and ids(failed["nodes"]) == [graph.ids["failed"]]
    later = succeeds(
        read_cli(
            graph.root,
            "digest",
            "--now",
            format_timestamp(NOW + timedelta(hours=26)),
            deleted=deleted,
        )
    )
    assert (
        next(row for row in later["items"] if row["kind"] == "failed_24h")["count"] == 0
    )


@pytest.mark.parametrize("deleted", [False, True])
def test_show_preserves_relations_instructions_pins_and_warnings(graph, deleted):
    shown = succeeds(
        read_cli(
            graph.root,
            "show",
            graph.ids["hypothesis"],
            deleted=deleted,
        )
    )
    assert shown == api(graph.root, "show", graph.ids["hypothesis"])
    assert len(shown["instructions"]) == 1
    assert shown["instructions"][0]["body"] == "inspect the evidence"
    assert len(shown["pins"]) == 1
    assert shown["pins"][0]["to"] == graph.ids["old_result"]
    prediction = succeeds(
        read_cli(
            graph.root,
            "show",
            graph.ids["equal"],
            deleted=deleted,
        )
    )
    assert prediction["warnings"] == ["non_discriminating"]


@pytest.mark.parametrize("deleted", [False, True])
def test_alternative_union_predictions_and_matrix_global_discrimination(graph, deleted):
    alternatives = succeeds(
        read_cli(
            graph.root,
            "alternatives",
            graph.ids["hypothesis"],
            deleted=deleted,
        )
    )
    assert ids(alternatives) == [graph.ids["alternative"], graph.ids["outside"]]
    assert ids(alternatives[0]["supports"]) == [graph.ids["claim"]]
    assert ids(alternatives[1]["contradicts"]) == [graph.ids["claim"]]
    predictions = succeeds(
        read_cli(
            graph.root,
            "predictions",
            graph.ids["hypothesis"],
            deleted=deleted,
        )
    )
    assert [row["source"]["id"] for row in predictions] == [
        graph.ids[k] for k in ("prediction", "single", "equal")
    ]
    assert [row["expected"] for row in predictions] == ["yes", "yes", "same"]
    matrix = succeeds(
        read_cli(graph.root, "matrix", graph.ids["question"], deleted=deleted)
    )
    assert ids(matrix["columns"]) == [graph.ids["hypothesis"], graph.ids["alternative"]]
    rows = {row["source"]["id"]: row for row in matrix["rows"]}
    assert set(rows) == {graph.ids[k] for k in ("prediction", "single", "equal")}
    assert not rows[graph.ids["prediction"]]["non_discriminating"]
    assert not rows[graph.ids["single"]]["non_discriminating"]
    assert rows[graph.ids["equal"]]["non_discriminating"]
    assert rows[graph.ids["single"]]["cells"] == {
        graph.ids["hypothesis"]: "yes",
        graph.ids["alternative"]: None,
    }


@pytest.mark.parametrize("deleted", [False, True])
def test_where_conjoins_repeated_fields_author_tags_text_and_budget(graph, deleted):
    arguments = (
        "--type",
        "Task",
        "--status",
        "active",
        "--author",
        "agent1",
        "--field",
        "kind=expansion",
        "--field",
        "command=a=b",
        "--tag",
        "x",
        "--text",
        "FiT",
        "--over-budget",
    )
    value = succeeds(read_cli(graph.root, "where", *arguments, deleted=deleted))
    assert ids(value) == [graph.ids["filter"]]
    assert value == api(
        graph.root,
        "where",
        type="Task",
        status="active",
        author="agent1",
        field={"kind": "expansion", "command": "a=b"},
        tag="x",
        text="FiT",
        over_budget=True,
    )
    for changed in (
        ("--tag", "missing"),
        ("--author", "absent"),
        ("--text", "absent"),
        ("--type", "Note"),
        ("--status", "done"),
    ):
        assert (
            succeeds(
                read_cli(
                    graph.root,
                    "where",
                    *arguments,
                    *changed,
                    deleted=deleted,
                )
            )
            == []
        )
    for key in ("kind", "command"):
        negative = tuple(
            key + "=missing" if value.startswith(key + "=") else value
            for value in arguments
        )
        assert succeeds(read_cli(graph.root, "where", *negative, deleted=deleted)) == []


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    "values",
    [
        ("kind=missing", "kind=expansion"),
        ("kind=expansion", "kind=missing"),
    ],
)
def test_where_rejects_conflicting_repeated_fields(graph, deleted, values):
    refused(
        read_cli(
            graph.root,
            "where",
            "--field",
            values[0],
            "--field",
            values[1],
            deleted=deleted,
        ),
        "schema",
    )


@pytest.mark.parametrize("deleted", [False, True])
def test_where_allows_identical_repeated_fields(graph, deleted):
    actual = succeeds(
        read_cli(
            graph.root,
            "where",
            "--field",
            "kind=expansion",
            "--field",
            "kind=expansion",
            deleted=deleted,
        )
    )
    assert actual == api(graph.root, "where", field={"kind": "expansion"})
    assert graph.ids["filter"] in ids(actual)


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    "fields,invalid",
    [
        (("kind=missing", "kind=expansion"), True),
        (("kind=expansion", "kind=missing"), True),
        (("missing-equals",), True),
        (("kind=expansion", "missing-equals"), True),
        (("kind=expansion", "kind=expansion"), False),
        (("command=a=b", "command=a=b", "kind=expansion"), False),
    ],
)
def test_where_field_api_installed_console_parity(graph, deleted, fields, invalid):
    from researchgraph.model import RuleViolation

    prepare_cache(graph.root, deleted)
    before = source_snapshot(graph.root)
    if invalid:
        with pytest.raises(RuleViolation) as caught:
            api(graph.root, "where", field=fields)
        assert caught.value.rule == "schema"
    else:
        expected = api(graph.root, "where", field=fields)
        assert graph.ids["filter"] in ids(expected)
        mapping = dict(entry.split("=", 1) for entry in fields)
        assert expected == api(graph.root, "where", field=mapping)
    assert source_snapshot(graph.root) == before

    prepare_cache(graph.root, deleted)
    result = run_console(
        [
            "--project",
            str(graph.root),
            "--json",
            "--now",
            format_timestamp(READ_NOW),
            "--read-only",
            "where",
            *[argument for entry in fields for argument in ("--field", entry)],
        ]
    )
    assert source_snapshot(graph.root) == before
    if invalid:
        refused(result, "schema")
    else:
        assert succeeds(result) == expected


@pytest.mark.parametrize("deleted", [False, True])
def test_as_of_approval_commit_timestamp_and_later_edge(graph, deleted):
    for boundary, status in [
        (graph.ids["before_approval"], "proposed"),
        (graph.ids["between"], "proposed"),
        (graph.ids["after_approval"], "active"),
        ("HEAD", "active"),
    ]:
        shown = succeeds(
            read_cli(
                graph.root,
                "show",
                graph.ids["hypothesis"],
                "--as-of",
                boundary,
                deleted=deleted,
            )
        )
        assert shown["status"] == status
        if boundary != "HEAD":
            assert shown["edges"] == []
    refused(
        read_cli(
            graph.root,
            "show",
            graph.ids["question"],
            "--as-of",
            graph.ids["before_approval"],
            deleted=deleted,
        ),
        "not_found",
    )


@pytest.mark.parametrize("deleted", [False, True])
def test_stale_deduplicates_independent_heads_paths_and_as_of(tmp_path, deleted):
    g = Graph(tmp_path / "project")
    try:
        r0 = g.node("r0", "Result")
        s0 = g.node("s0", "Result")
        bridge = g.node("bridge", "Result")
        g.edge("derived-from", bridge, r0)
        c = g.node("c", "Claim")
        for result in (r0, s0, bridge):
            g.edge("cites", c, result)
        g.write("approve", c, role="human")
        r1 = g.write("supersede", r0, inherit_edges=False)["id"]
        g.edge("cites", c, r1)
        one_head = git(g.root, "rev-parse", "HEAD")
        r2 = g.write("supersede", r1, inherit_edges=False)["id"]
        s1 = g.write("supersede", s0, inherit_edges=False)["id"]
        for boundary, heads in [(one_head, [r1]), ("HEAD", sorted([r2, s1]))]:
            value = succeeds(
                read_cli(g.root, "stale", "--as-of", boundary, deleted=deleted)
            )
            assert ids(value["items"]) == [c]
            assert value["items"][0]["responsible"] == heads
        inherited = g.write("supersede", c)["id"]
        stale = succeeds(read_cli(g.root, "stale", deleted=deleted))["items"]
        assert ids(stale) == [inherited]
        assert stale[0]["responsible"] == sorted([r2, s1])
        current = g.write("supersede", inherited, inherit_edges=False)["id"]
        g.edge("cites", current, r2)
        g.edge("cites", current, s1)
        g.write("approve", current, role="human")
        assert succeeds(read_cli(g.root, "stale", deleted=deleted)) == {"items": []}
    finally:
        g.close()


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("resolution", ["complete", "retire"])
def test_blocked_disappears_when_blocker_finishes_or_edge_retires(
    tmp_path, deleted, resolution
):
    g = Graph(tmp_path / "project")
    try:
        blocker = g.node("blocker", "Task")
        target = g.node("target", "Task")
        g.edge("blocks", blocker, target)
        before = succeeds(read_cli(g.root, "blocked", deleted=deleted))["items"]
        assert [(row["id"], row["blocking"]) for row in before] == [(target, [blocker])]
        if resolution == "complete":
            g.write("claim", blocker)
            g.write("complete", blocker, "done")
        else:
            g.write("retire", blocker, "no longer needed")
        assert succeeds(read_cli(g.root, "blocked", deleted=deleted)) == {"items": []}
        assert target in ids(
            succeeds(read_cli(g.root, "ready", deleted=deleted))["items"]
        )
    finally:
        g.close()


@pytest.mark.parametrize("deleted", [False, True])
def test_since_observes_late_approval_and_edge_retirement(tmp_path, deleted):
    g = Graph(tmp_path / "project")
    try:
        h = g.node("hypothesis", "Hypothesis")
        task = g.node("task", "Task")
        edge = g.edge("tests", task, h, fields={"expected": "yes"})
        cutoff = format_timestamp(NOW + timedelta(seconds=g.seconds + 1))
        g.write("approve", h, role="human")
        g.write("retire", task, "obsolete")
        changed = succeeds(read_cli(g.root, "since", cutoff, deleted=deleted))
        assert {h, task, edge["id"]} <= set(ids(changed))
        assert next(row for row in changed if row["id"] == edge["id"])["retired"]
        assert changed == api(g.root, "since", cutoff)
    finally:
        g.close()


@pytest.mark.parametrize("name", [*ID_QUERIES, "since"])
def test_query_required_positional_argument(graph, name):
    result = read_cli(graph.root, name)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Missing argument" in result.stderr


@pytest.mark.parametrize("name", ID_QUERIES)
@pytest.mark.parametrize("deleted", [False, True])
def test_query_missing_node_is_structured(graph, name, deleted):
    refused(read_cli(graph.root, name, "ta-zzzzzzzz", deleted=deleted), "not_found")


@pytest.mark.parametrize("name", QUERY_ARGUMENTS)
@pytest.mark.parametrize("deleted", [False, True])
def test_query_invalid_as_of_is_structured(graph, name, deleted):
    key = QUERY_ARGUMENTS[name]
    args = () if key is None else (graph.ids[key],)
    refused(
        read_cli(graph.root, name, *args, "--as-of", "not-a-revision", deleted=deleted),
        "schema",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("where", "--field", "missing-equals"),
        ("since", "not-a-time"),
        ("ready", "--now", "invalid"),
        ("ready", "--now", ""),
        ("ready", "--as-agent", ":invalid"),
    ],
)
@pytest.mark.parametrize("deleted", [False, True])
def test_query_malformed_context_and_filters_preserve_source(graph, arguments, deleted):
    refused(read_cli(graph.root, *arguments, deleted=deleted), "schema")


@pytest.mark.parametrize("name", QUERY_ARGUMENTS)
def test_query_project_not_found_exit_three(graph, tmp_path, name):
    key = QUERY_ARGUMENTS[name]
    args = () if key is None else (graph.ids[key],)
    result = invoke(tmp_path / "absent", name, *args)
    assert result.exit_code == 3
    assert result.stdout == ""
    value = json.loads(result.stderr)
    assert value["rule"] == "project_not_found"
    assert isinstance(value["message"], str) and isinstance(value["ids"], list)


def test_query_invalid_project_exit_three(tmp_path):
    root = repository(tmp_path / "invalid")
    metadata = root / ".researchgraph"
    metadata.mkdir()
    (metadata / "profile.yaml").write_text("[]\n", encoding="utf-8")
    result = invoke(root, "ready")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert json.loads(result.stderr)["rule"] == "invalid_project"
    assert (metadata / "profile.yaml").read_text(encoding="utf-8") == "[]\n"


def test_query_project_discovery_from_subdirectory(graph, monkeypatch):
    prepare_cache(graph.root, True)
    before = source_snapshot(graph.root)
    monkeypatch.chdir(graph.root / "artifacts")
    result = succeeds(invoke(None, "ready", "--read-only", now=READ_NOW))
    assert source_snapshot(graph.root) == before
    assert result == api(graph.root, "ready")


@pytest.mark.parametrize("name", QUERY_ARGUMENTS)
@pytest.mark.parametrize("deleted", [False, True])
def test_query_human_tables_use_installed_console(graph, name, deleted):
    key = QUERY_ARGUMENTS[name]
    args = () if key is None else (graph.ids[key],)
    prepare_cache(graph.root, deleted)
    before = source_snapshot(graph.root)
    result = run_console(
        [
            "--project",
            str(graph.root),
            "--now",
            format_timestamp(READ_NOW),
            "--read-only",
            name,
            *args,
        ]
    )
    assert source_snapshot(graph.root) == before
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    lines = result.stdout.strip().splitlines()
    assert len(lines) >= 2
    assert "\t" in lines[0]
    marker = {
        "ready": graph.ids["blocker"],
        "blocked": graph.ids["blocked"],
        "queue": graph.ids["pending"],
        "stale": graph.ids["claim"],
        "digest": "failed_24h",
        "show": graph.ids["running"],
        "history": graph.ids["old_result"],
        "evidence": graph.ids["old_result"],
        "weakest": graph.ids["old_result"],
        "alternatives": graph.ids["alternative"],
        "predictions": graph.ids["prediction"],
        "matrix": graph.ids["hypothesis"],
        "notes": graph.ids["last_note"],
        "subtree": graph.ids["parent"],
        "since": graph.ids["hypothesis"],
        "where": graph.ids["filter"],
    }[name]
    assert marker in result.stdout
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
