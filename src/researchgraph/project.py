"""File-backed projects and the shared low-level write boundary."""

import re
import subprocess
from copy import deepcopy
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from researchgraph.model import (
    Actor,
    Edge,
    Node,
    RuleViolation,
    Status,
    Write,
    format_timestamp,
)
from researchgraph.profiles import Profile, version_tuple
from researchgraph.store.git import Git
from researchgraph.store.io import dump_edge, dump_node, load_edge, load_node
from researchgraph.store.transaction import atomic_write, write_lock

_IDENTIFIER = re.compile(r"[a-z]{2,4}-[a-z2-7]{8}", re.ASCII)


class Project:
    def __init__(self, path: Path, profile: Profile, version: str, read_only: bool):
        self.path = path
        self.profile = profile
        self.profile_name = profile.name
        self.profile_version = version
        self.read_only = read_only
        self._git = Git(path)

    @classmethod
    def open(cls, path, read_only=False) -> "Project":
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise RuleViolation("project_not_found", f"Project not found: {root}")
        profile, version = cls._load_profile(root)
        project = cls(root, profile, version, read_only)
        project._refresh()
        return project

    @staticmethod
    def _load_profile(root):
        try:
            pin = root / ".researchgraph/profile.yaml"
            if pin.is_symlink() or (root / ".researchgraph").is_symlink():
                raise ValueError("Profile path must not be a symlink")
            record = yaml.safe_load(pin.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or set(record) != {"name", "version"}:
                raise ValueError("Profile pin must contain name and version")
            if not isinstance(record["name"], str):
                raise TypeError("Profile name must be a string")
            version = record["version"]
            ordering = version_tuple(version)
            profile = Profile.load(record["name"])
            if ordering > version_tuple(profile.version):
                raise RuleViolation(
                    "profile_too_new",
                    f"Profile {profile.name} {version} is newer than installed {profile.version}",
                )
            return profile, version
        except RuleViolation as exc:
            if exc.rule == "profile_too_new":
                raise
            raise RuleViolation("invalid_project", str(exc)) from exc
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            raise RuleViolation(
                "invalid_project", f"Invalid profile pin: {exc}"
            ) from exc

    def _refresh(self):
        """Public-entry refresh boundary; T06 can extend it with index refresh.

        Currently reads only source layout/pin, never creates a cache or lock.
        Rechecking the pin also catches a long-lived project's profile changing.
        """
        if not self._git.exists():
            raise RuleViolation("invalid_project", "Project is not in a git repository")
        for relative in (".researchgraph", "graph", "graph/nodes", "graph/edges"):
            path = self._safe_path(relative)
            # Git does not preserve empty graph directories. Missing graph
            # directories describe an empty collection, not an invalid clone.
            if (relative == ".researchgraph" or path.exists()) and not path.is_dir():
                raise RuleViolation(
                    "invalid_project", f"Invalid project directory: {relative}"
                )
        profile, version = self._load_profile(self.path)
        if (profile.name, version) != (self.profile_name, self.profile_version):
            self.profile = profile
            self.profile_name, self.profile_version = profile.name, version

    @classmethod
    def init(
        cls,
        path,
        profile="research",
        *,
        actor,
        now,
        role=None,
        commit=True,
        read_only=False,
    ) -> "Project":
        if read_only:
            raise RuleViolation("read_only", "Project is read-only")
        root = Path(path).expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise RuleViolation("invalid_project", "Project path is not a directory")
        cls._actor(actor)
        try:
            Git(root).check_clock(now)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuleViolation("schema", f"Invalid write time: {exc}") from exc
        if actor.kind != "human":
            raise RuleViolation(
                "human_only_approval", "Only humans may initialise projects"
            )
        installed = Profile.load(profile)
        project = cls(root, installed, installed.version, False)
        change = Write("init", actor, role, now, {}, [], [])
        project._validate(change)
        if (root / ".researchgraph/profile.yaml").exists():
            raise RuleViolation("already_initialised", "Project is already initialised")
        for relative in (
            ".researchgraph",
            ".researchgraph/profile.yaml",
            ".researchgraph/lock",
            ".gitignore",
            "graph",
            "graph/nodes",
            "graph/edges",
            "artifacts",
        ):
            project._safe_path(relative)
        for relative in (
            ".researchgraph",
            "graph",
            "graph/nodes",
            "graph/edges",
            "artifacts",
        ):
            destination = root / relative
            if destination.exists() and not destination.is_dir():
                raise RuleViolation("invalid_project", f"Not a directory: {relative}")
        if (root / ".gitignore").exists() and not (root / ".gitignore").is_file():
            raise RuleViolation("invalid_project", ".gitignore is not a file")
        # All caller validation has completed before mkdir or git init.
        paths = [".researchgraph/profile.yaml", ".gitignore"]
        try:
            root.mkdir(parents=True, exist_ok=True)
            metadata = root / ".researchgraph"
            if metadata.is_symlink():
                raise RuleViolation("schema", "Project metadata must not be a symlink")
            metadata.mkdir(exist_ok=True)
            with write_lock(root):
                if (metadata / "profile.yaml").exists():
                    raise RuleViolation(
                        "already_initialised", "Project is already initialised"
                    )
                project._git.check_clock(now)
                # Reject unsafe pre-existing destinations before git init.
                for relative in (
                    *paths,
                    "graph",
                    "graph/nodes",
                    "graph/edges",
                    "artifacts",
                ):
                    project._safe_path(relative)
                ignore = root / ".gitignore"
                content = ignore.read_bytes() if ignore.exists() else b""
                if content and not content.endswith(b"\n"):
                    content += b"\n"
                content += b".researchgraph/index.sqlite\n.researchgraph/lock\n"
                project._git.init()
                for relative in ("graph/nodes", "graph/edges", "artifacts"):
                    (root / relative).mkdir(parents=True, exist_ok=True)
                atomic_write(
                    root,
                    {
                        paths[
                            0
                        ]: f'name: {installed.name}\nversion: "{installed.version}"\n'.encode(),
                        paths[1]: content,
                    },
                )
                if commit:
                    commit_id = project._commit(paths, change)
                    project._events(change, commit_id)
        except RuleViolation:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuleViolation(
                "write_failed", f"Initialisation failed: {exc}", paths
            ) from exc
        return project

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise RuleViolation("schema", f"Invalid graph identifier: {value}")

    def _safe_path(self, relative):
        if not isinstance(relative, str):
            raise RuleViolation("schema", "Paths must be relative strings")
        parts = PurePosixPath(relative)
        if (
            not relative
            or parts.is_absolute()
            or str(parts) != relative
            or any(part in (".", "..") for part in parts.parts)
            or "\\" in relative
            or "\x00" in relative
        ):
            raise RuleViolation("schema", f"Unsafe project path: {relative}")
        path = self.path
        for part in parts.parts:
            path = path / part
            if path.is_symlink():
                raise RuleViolation("schema", f"Symlink project path: {relative}")
        if not path.resolve().is_relative_to(self.path):
            raise RuleViolation("schema", f"Path escapes project: {relative}")
        return path

    def _read(self, relative, edge=False):
        path = self._safe_path(relative)
        if not path.is_file():
            raise RuleViolation(
                "not_found", f"Graph record not found: {path.stem}", [path.stem]
            )
        try:
            value = (
                Edge.from_dict(load_edge(path))
                if edge
                else Node.from_dict(load_node(path))
            )
            self._identifier(value.id)
            if value.id != path.stem:
                raise ValueError("Graph id does not match filename")
            return value
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            raise RuleViolation(
                "invalid_project", f"Invalid graph record {relative}: {exc}"
            ) from exc

    def get_node(self, id: str) -> Node:
        self._refresh()
        self._identifier(id)
        return self._read(f"graph/nodes/{id}.yaml")

    def get_nodes(self) -> list[Node]:
        self._refresh()
        return [
            self._read(f"graph/nodes/{path.name}")
            for path in sorted((self.path / "graph/nodes").glob("*.yaml"))
        ]

    def get_edges(self) -> list[Edge]:
        self._refresh()
        return [
            self._read(f"graph/edges/{path.name}", edge=True)
            for path in sorted((self.path / "graph/edges").glob("*.yaml"))
        ]

    @staticmethod
    def _actor(actor):
        if (
            not isinstance(actor, Actor)
            or actor.kind not in ("human", "agent")
            or not isinstance(actor.name, str)
            or not actor.name.strip()
            or any(character in actor.name for character in ("\n", "\r", "\x00"))
            or any(
                value is not None and not isinstance(value, str)
                for value in (actor.email, actor.model, actor.version)
            )
        ):
            raise RuleViolation("schema", "Invalid actor")

    def _validate(self, write: Write):
        """One schema/profile boundary; full graph invariants belong to T07."""
        if self.read_only:
            raise RuleViolation("read_only", "Project is read-only")
        try:
            self._git.check_clock(write.now)
            self._actor(write.actor)
            if not isinstance(write.op, str) or not write.op or "\n" in write.op:
                raise RuleViolation("schema", "Invalid operation name")
            if write.role is not None and write.role not in self.profile.roles:
                raise RuleViolation("wrong_role", f"Unknown role: {write.role}")
            if not isinstance(write.args, dict):
                raise RuleViolation("schema", "Write arguments must be a mapping")
            nodes, edges = {}, set()
            for node in write.nodes:
                self._identifier(node.id)
                definition = self.profile.node_type(node.type)
                if not node.id.startswith(definition.prefix + "-") or node.id in nodes:
                    raise RuleViolation(
                        "schema", "Invalid or duplicate node id", [node.id]
                    )
                nodes[node.id] = node
                self._safe_path(f"graph/nodes/{node.id}.yaml")
                self._actor(node.author)
                if (
                    not isinstance(node.title, str)
                    or len(node.title) > 200
                    or any(
                        c in node.title for c in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
                    )
                ):
                    raise RuleViolation(
                        "schema",
                        "Title must be single-line and at most 200 characters",
                        [node.id],
                    )
                if not isinstance(node.body, str) or node.status not in list(Status):
                    raise RuleViolation(
                        "schema", "Invalid node body or status", [node.id]
                    )
                if (
                    not isinstance(node.tags, list)
                    or any(not isinstance(tag, str) for tag in node.tags)
                    or node.tags != sorted(set(node.tags))
                ):
                    raise RuleViolation(
                        "schema", "Tags must be sorted unique strings", [node.id]
                    )
                if node.priority is not None and type(node.priority) is not int:
                    raise RuleViolation(
                        "schema", "Priority must be an integer", [node.id]
                    )
                for dimensions in (node.budget, node.usage):
                    if dimensions is not None and (
                        not isinstance(dimensions, dict)
                        or any(
                            key not in ("tokens", "compute_seconds")
                            or type(value) is not int
                            or value < 0
                            for key, value in dimensions.items()
                        )
                    ):
                        raise RuleViolation(
                            "schema", "Invalid budget or usage", [node.id]
                        )
                for approval in node.approvals:
                    self._actor(approval.actor)
                    format_timestamp(approval.at)
                    if (
                        not isinstance(approval.what, str)
                        or (
                            approval.note is not None
                            and not isinstance(approval.note, str)
                        )
                        or (
                            approval.role is not None
                            and not isinstance(approval.role, str)
                        )
                    ):
                        raise RuleViolation("schema", "Invalid approval", [node.id])
                for artifact in node.artifacts:
                    self._artifact_path(artifact.path, node.id)
                    self._actor(artifact.added_by)
                    format_timestamp(artifact.added_at)
                    if (
                        type(artifact.bytes) is not int
                        or artifact.bytes < 0
                        or not isinstance(artifact.sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None
                    ):
                        raise RuleViolation(
                            "schema", "Invalid artifact record", [node.id]
                        )
                if node.lease is not None:
                    self._actor(node.lease.actor)
                    format_timestamp(node.lease.acquired_at)
                    format_timestamp(node.lease.expires_at)
                self._fields(node, definition, edge=False)
                Node.from_dict(node.to_dict())
            for edge in write.edges:
                self._identifier(edge.id)
                if not edge.id.startswith("ed-") or edge.id in edges:
                    raise RuleViolation(
                        "schema", "Invalid or duplicate edge id", [edge.id]
                    )
                edges.add(edge.id)
                self._safe_path(f"graph/edges/{edge.id}.yaml")
                self._actor(edge.author)
                definition = self.profile.edge_type(edge.type)
                for endpoint in (edge.from_, edge.to):
                    self._identifier(endpoint)
                    if endpoint not in nodes:
                        self.get_node(endpoint)
                if type(edge.retired) is not bool:
                    raise RuleViolation("schema", "Retired must be boolean", [edge.id])
                self._fields(edge, definition, edge=True)
                Edge.from_dict(edge.to_dict())
            self._artifact_paths(write)
            for validator in self.profile.validators:
                validator(self, write)
        except RuleViolation:
            raise
        except (
            TypeError,
            ValueError,
            AttributeError,
            KeyError,
            ValidationError,
        ) as exc:
            raise RuleViolation("schema", f"Invalid write: {exc}") from exc

    def _fields(self, record, definition, *, edge):
        path = self.path / f"graph/{'edges' if edge else 'nodes'}/{record.id}.yaml"
        if not path.exists():
            record.fields = self.profile.normalize_fields(
                record.type, record.fields, edge=edge
            )
        else:
            # Never materialise newly installed defaults into existing records.
            if not isinstance(record.fields, dict):
                raise RuleViolation("schema", "Fields must be a mapping", [record.id])
            validator_for(definition.schema)(definition.schema).validate(record.fields)

    def _artifact_path(self, relative, node_id=None):
        try:
            path = self._safe_path(relative)
        except RuleViolation as exc:
            raise RuleViolation("artifact_path", exc.message, [str(relative)]) from exc
        parts = PurePosixPath(relative).parts
        if (
            len(parts) < 3
            or parts[0] != "artifacts"
            or (node_id is not None and parts[1] != node_id)
        ):
            raise RuleViolation(
                "artifact_path",
                "Artifacts must be under artifacts/<node id>/",
                [relative],
            )
        try:
            self._identifier(parts[1])
        except RuleViolation as exc:
            raise RuleViolation("artifact_path", exc.message, [relative]) from exc
        return path

    def _artifact_paths(self, write):
        paths = write.args.get("artifacts", [])
        if not isinstance(paths, list):
            raise RuleViolation("schema", "Artifacts must be a list of paths")
        paths = list(paths)
        for node in write.nodes:
            existing = self.path / f"graph/nodes/{node.id}.yaml"
            old = (
                self._read(f"graph/nodes/{node.id}.yaml").artifacts
                if existing.exists()
                else []
            )
            paths.extend(artifact.path for artifact in node.artifacts[len(old) :])
        for relative in paths:
            if not self._artifact_path(relative).is_file():
                raise RuleViolation(
                    "artifact_missing", f"Artifact not found: {relative}", [relative]
                )
        return list(dict.fromkeys(paths))

    def _commit(self, paths, write):
        identifier = write.args.get("node")
        if identifier is None:
            identifier = next(
                (record.id for record in [*write.nodes, *write.edges]), None
            )
        message = f"rg: {write.op}" + (f" {identifier}" if identifier else "")
        try:
            return self._git.commit(paths, message, write.actor, write.now)
        except (OSError, subprocess.SubprocessError, RuleViolation) as exc:
            raise RuleViolation(
                "write_failed", f"Git commit failed: {exc}", paths
            ) from exc

    def _events(self, write, commit_id):
        for hook in self.profile.event_hooks:
            try:
                hook(self, write, commit_id)
            except Exception as exc:
                paths = [f"graph/nodes/{node.id}.yaml" for node in write.nodes]
                paths += [f"graph/edges/{edge.id}.yaml" for edge in write.edges]
                if write.op == "init":
                    paths = [".researchgraph/profile.yaml", ".gitignore"]
                raise RuleViolation(
                    "write_failed",
                    f"Event hook failed after commit {commit_id}: {exc}",
                    paths,
                ) from exc

    def apply(self, write: Write, commit=True) -> str | None:
        if self.read_only:
            raise RuleViolation("read_only", "Project is read-only")
        self._git.check_clock(write.now)
        self._refresh()
        paths = []
        try:
            with write_lock(self.path):
                self._git.check_clock(write.now)
                self._refresh()
                change = deepcopy(write)
                self._validate(change)
                files = {}
                try:
                    for node in change.nodes:
                        files[f"graph/nodes/{node.id}.yaml"] = dump_node(
                            node.to_dict()
                        ).encode("utf-8")
                    for edge in change.edges:
                        files[f"graph/edges/{edge.id}.yaml"] = dump_edge(
                            edge.to_dict()
                        ).encode("utf-8")
                except (TypeError, ValueError, yaml.YAMLError) as exc:
                    raise RuleViolation(
                        "schema", f"Cannot encode graph records: {exc}"
                    ) from exc
                artifacts = self._artifact_paths(change)
                paths = [*files, *artifacts]
                for relative in files:
                    self._safe_path(relative).parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.path, files)
                if not commit:
                    return None
                commit_id = self._commit(paths, change)
                self._events(change, commit_id)
                return commit_id
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuleViolation("write_failed", f"Write failed: {exc}", paths) from exc
