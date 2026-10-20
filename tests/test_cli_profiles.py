"""T15: installed profile entry points and defaults across real CLI processes."""

from copy import deepcopy

import pytest

from researchgraph.profiles.base import Profile
from researchgraph.project import Project
from researchgraph.store.io import dump_edge, dump_node, load_edge, load_node
from tests.helpers import git, repository
from tests.test_cli import invoke, refused, snapshot, succeeds

pytestmark = [pytest.mark.cli, pytest.mark.usefixtures("cli_cache_policy")]


def call(root, mode, *arguments):
    if mode == "deleted":
        (root / ".researchgraph/index.sqlite").unlink(missing_ok=True)
    return invoke(root, *arguments)


@pytest.mark.parametrize(
    ("profile", "rule"),
    [
        ("test-invalid-location", "unsupported_schema_default"),
        ("test-invalid-default", "invalid_schema_default"),
        ("test-invalid-null-default", "invalid_schema_default"),
        *[
            ("test-default-" + name, "unsupported_schema_default")
            for name in (
                "nested",
                "items",
                "defs",
                "root",
                "anyof",
                "oneof",
                "if",
                "then",
                "else",
            )
        ],
    ],
)
def test_installed_invalid_profiles_fail_before_destination_creation(
    tmp_path, profile, rule
):
    repository(tmp_path)
    target = tmp_path / "destination"
    refused(invoke(target, "init", "--profile", profile), rule)
    assert not target.exists()


def test_installed_profile_defaults_closed_schemas_and_validator(tmp_path, index_mode):
    repository(tmp_path)
    root = tmp_path / "project"
    succeeds(invoke(root, "init", "--profile", "test-cli-contract"))
    first = succeeds(call(root, index_mode, "propose", "Task", "first"))
    second = succeeds(
        call(
            root, index_mode, "propose", "Task", "second", "--fields", '{"mode":"safe"}'
        )
    )
    nulls = succeeds(
        call(
            root,
            index_mode,
            "propose",
            "Task",
            "null",
            "--fields",
            '{"mode":null,"settings":null}',
        )
    )
    assert first["fields"] == {"mode": "fast", "settings": {"enabled": True}}
    assert second["fields"] == {"mode": "safe", "settings": {"enabled": True}}
    assert nulls["fields"] == first["fields"]
    edge = succeeds(
        call(root, index_mode, "link", "depends", first["id"], second["id"])
    )
    assert edge["fields"] == {"weight": 2}
    incident = succeeds(
        call(
            root,
            index_mode,
            "propose",
            "Task",
            "incident",
            "--edge",
            '{"type":"depends","to":"' + second["id"] + '","fields":{"weight":null}}',
        )
    )
    with Project.open(root) as project:
        assert project.get_node(first["id"]).to_dict() == first
        assert project.get_node(second["id"]).to_dict() == second
        assert next(
            e for e in project.get_edges() if e.from_ == incident["id"]
        ).fields == {"weight": 2}
    before = snapshot(root)
    for arguments, rule in [
        (["propose", "Task", "bad", "--fields", '{"extra":1}'], "schema"),
        (["propose", "Task", "bad", "--fields", '{"mode":"invalid"}'], "schema"),
        (
            ["propose", "Task", "forbidden", "--fields", '{"mode":"forbidden"}'],
            "fixture_validation",
        ),
        (
            ["link", "depends", second["id"], first["id"], "--fields", '{"extra":1}'],
            "schema",
        ),
        (
            [
                "link",
                "depends",
                second["id"],
                first["id"],
                "--fields",
                '{"weight":"bad"}',
            ],
            "schema",
        ),
    ]:
        refused(call(root, index_mode, *arguments), rule)
        assert snapshot(root) == before
    path = root / "graph/nodes" / (first["id"] + ".yaml")
    value = load_node(path)
    value["fields"] = {}
    path.write_text(dump_node(value))
    edge_path = root / "graph/edges" / (edge["id"] + ".yaml")
    value = load_edge(edge_path)
    value["fields"] = {}
    edge_path.write_text(dump_edge(value))
    raw, raw_edge, head = (
        path.read_bytes(),
        edge_path.read_bytes(),
        git(root, "rev-parse", "HEAD"),
    )
    assert succeeds(call(root, index_mode, "show", first["id"]))["fields"] == {}
    assert succeeds(call(root, index_mode, "show", edge["id"]))["fields"] == {}
    assert (
        path.read_bytes(),
        edge_path.read_bytes(),
        git(root, "rev-parse", "HEAD"),
    ) == (raw, raw_edge, head)
    replacement = succeeds(
        call(root, index_mode, "supersede", first["id"], "--fields", "{}")
    )
    assert replacement["fields"] == first["fields"]
    assert (root / "graph/nodes" / (replacement["id"] + ".yaml")).is_file()


def test_composed_defaults_are_validated_together_before_cli_write(
    tmp_path, index_mode
):
    repository(tmp_path)
    root = tmp_path / "project"
    succeeds(invoke(root, "init", "--profile", "test-composed-defaults"))
    before = snapshot(root)
    refused(call(root, index_mode, "propose", "Task", "invalid defaults"), "schema")
    assert snapshot(root) == before
    valid = succeeds(
        call(
            root, index_mode, "propose", "Task", "valid", "--fields", '{"mode":"safe"}'
        )
    )
    assert valid["fields"] == {"mode": "safe", "settings": {"enabled": True}}


def test_mutable_defaults_and_composition_do_not_mutate_installed_schema(
    tmp_path, index_mode
):
    repository(tmp_path)
    root = tmp_path / "project"
    name = "test-composed-defaults"
    schema = deepcopy(Profile.load(name).node_types[0].schema)
    succeeds(invoke(root, "init", "--profile", name))
    first = succeeds(
        call(
            root, index_mode, "propose", "Task", "first", "--fields", '{"mode":"safe"}'
        )
    )
    second = succeeds(
        call(
            root, index_mode, "propose", "Task", "second", "--fields", '{"mode":"safe"}'
        )
    )
    first["fields"]["settings"]["enabled"] = False
    assert second["fields"]["settings"] == {"enabled": True}
    assert succeeds(call(root, index_mode, "show", first["id"]))["fields"][
        "settings"
    ] == {"enabled": True}
    assert Profile.load(name).node_types[0].schema == schema
