"""Accepted profile names retain their exact identity in project pins."""

from datetime import UTC, datetime

import pytest
import yaml

from researchgraph import Project
from researchgraph.model import Actor
from researchgraph.profiles import Profile

NOW = datetime(2026, 10, 19, tzinfo=UTC)
HUMAN = Actor("human", "profile-pin-reviewer")


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(Profile, "_registry", dict(Profile._registry))


@pytest.mark.parametrize(
    "name",
    [
        "on",
        "yes",
        "research # custom",
        "연구 프로필",
        "research\ncustom",
        "research\u2028custom",
    ],
    ids=["on", "yes", "comment", "unicode", "multiline", "unicode-line-separator"],
)
@pytest.mark.parametrize("delete_index", [False, True], ids=["kept", "cold"])
def test_registered_profile_pin_round_trip(tmp_path, name, delete_index):
    profile = Profile.load("research")
    profile.name = name
    Profile.register(profile)
    assert Profile.load(name).name == name

    root = tmp_path / "source"
    pin = root / ".researchgraph/profile.yaml"
    initial_archive = tmp_path / "initial.tar.zst"
    with Project.init(
        root, profile=name, actor=HUMAN, now=NOW, commit=False
    ) as project:
        assert project.profile_name == name
        assert yaml.safe_load(pin.read_text(encoding="utf-8")) == {
            "name": name,
            "version": profile.version,
        }
        first = project.propose(
            "Note", "First note", actor=HUMAN, now=NOW, commit=False
        )
        project.export(initial_archive, actor=HUMAN, now=NOW)

    if delete_index:
        (root / ".researchgraph/index.sqlite").unlink()

    reopened_archive = tmp_path / "reopened.tar.zst"
    final_archive = tmp_path / "final.tar.zst"
    with Project.open(root) as project:
        assert project.profile_name == project.profile.name == name
        assert project.profile_version == profile.version
        assert project.get_node(first["id"]).title == "First note"
        project.export(reopened_archive, actor=HUMAN, now=NOW)
        assert reopened_archive.read_bytes() == initial_archive.read_bytes()
        second = project.propose(
            "Note", "Second note", actor=HUMAN, now=NOW, commit=False
        )
        project.export(final_archive, actor=HUMAN, now=NOW)

    restored_root = tmp_path / "restored"
    with Project.import_(
        final_archive, restored_root, actor=HUMAN, now=NOW, commit=False
    ) as restored:
        assert restored.profile_name == restored.profile.name == name
        assert restored.profile_version == profile.version
        assert (
            restored_root / ".researchgraph/profile.yaml"
        ).read_bytes() == pin.read_bytes()
        assert {node.id: node.title for node in restored.get_nodes()} == {
            first["id"]: "First note",
            second["id"]: "Second note",
        }

    if delete_index:
        (restored_root / ".researchgraph/index.sqlite").unlink()

    with Project.open(restored_root) as restored:
        assert restored.profile_name == restored.profile.name == name
        assert restored.get_node(second["id"]).title == "Second note"
        third = restored.propose(
            "Note", "Restored note", actor=HUMAN, now=NOW, commit=False
        )
        assert restored.get_node(third["id"]).title == "Restored note"


def test_default_profile_pin_bytes_are_unchanged(tmp_path):
    with Project.init(
        tmp_path / "default", actor=HUMAN, now=NOW, commit=False
    ) as project:
        assert (project.path / ".researchgraph/profile.yaml").read_bytes() == (
            b'name: research\nversion: "1.0"\n'
        )
