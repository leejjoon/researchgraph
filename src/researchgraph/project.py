"""File-backed projects and the shared low-level write boundary."""

import re
import sqlite3
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
from researchgraph.operations import _approve, _link, _propose, _reject
from researchgraph.profiles import Profile, version_tuple
from researchgraph.queries import QueryView, evaluation_time
from researchgraph.store.git import Git
from researchgraph.store.index import Index
from researchgraph.store.io import dump_edge, dump_node, load_edge, load_node
from researchgraph.store.transaction import atomic_write, write_lock
from researchgraph.validation import (
    validate_edge_endpoints,
    validate_permissions,
    validate_references,
    validate_write,
)

_IDENTIFIER = re.compile(r"[a-z]{2,4}-[a-z2-7]{8}", re.ASCII)


class Project:
    def __init__(self, path: Path, profile: Profile, version: str, read_only: bool):
        self.path = path
        self.profile = profile
        self.profile_name = profile.name
        self.profile_version = version
        self.read_only = read_only
        self._closed = False
        self._git = Git(path)
        self._pin_bytes = None
        self.index = Index(self)

    @classmethod
    def open(cls, path, read_only=False) -> "Project":
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise RuleViolation("project_not_found", f"Project not found: {root}")
        profile, version = cls._load_profile(root)
        project = cls(root, profile, version, read_only)
        project._pin_bytes = (root / ".researchgraph/profile.yaml").read_bytes()
        try:
            project._refresh()
        except BaseException:
            project.close()
            raise
        return project

    def close(self):
        """Release the disposable index and its private file-hashing helpers."""
        if not self._closed:
            try:
                self.index.close()
            finally:
                self._closed = True

    def __enter__(self):
        if self._closed:
            raise ValueError("Project is closed")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

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
        """Refresh profile bytes and the disposable index without a writer lock."""
        if self._closed:
            raise ValueError("Project is closed")
        # The mandatory index content scan validates worktree membership while
        # reading HEAD, avoiding a second git subprocess for every read.
        for relative in (".researchgraph", "graph", "graph/nodes", "graph/edges"):
            path = self._safe_path(relative)
            # Git does not preserve empty graph directories. Missing graph
            # directories describe an empty collection, not an invalid clone.
            if (relative == ".researchgraph" or path.exists()) and not path.is_dir():
                raise RuleViolation(
                    "invalid_project", f"Invalid project directory: {relative}"
                )
        pin = self._safe_path(".researchgraph/profile.yaml")
        try:
            pin_bytes = pin.read_bytes()
        except OSError as exc:
            raise RuleViolation(
                "invalid_project", f"Invalid profile pin: {exc}"
            ) from exc
        if pin_bytes != self._pin_bytes:
            profile, version = self._load_profile(self.path)
            self.profile = profile
            self.profile_name, self.profile_version = profile.name, version
            self._pin_bytes = pin_bytes
        self.index.refresh()

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
        project._refresh()
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
        return self.index.get_node(id)

    def get_nodes(self) -> list[Node]:
        self._refresh()
        return self.index.get_nodes()

    def get_edges(self) -> list[Edge]:
        self._refresh()
        return self.index.get_edges()

    def propose(
        self,
        type,
        title,
        body="",
        fields=None,
        edges=None,
        *,
        actor,
        role=None,
        now,
        commit=True,
    ):
        """Create a gate-initialized node and its incident edges atomically."""
        return self._operation(
            "propose",
            {
                "type": type,
                "title": title,
                "body": body,
                "fields": fields,
                "edges": edges,
            },
            actor,
            role,
            now,
            commit,
            _propose,
        )

    def link(self, type, from_, to, fields=None, *, actor, role=None, now, commit=True):
        """Create a non-reserved relation between two existing nodes."""
        return self._operation(
            "link",
            {"type": type, "from": from_, "to": to, "fields": fields},
            actor,
            role,
            now,
            commit,
            _link,
        )

    def approve(
        self, node, what="activate", note=None, *, actor, role=None, now, commit=True
    ):
        """Append an approval and activate once all matching gates are met."""
        return self._operation(
            "approve",
            {"node": node, "what": what, "note": note},
            actor,
            role,
            now,
            commit,
            _approve,
        )

    def reject(self, node, reason, *, actor, role=None, now, commit=True):
        """Reject a proposal and retire all its live incident relations."""
        return self._operation(
            "reject",
            {"node": node, "reason": reason},
            actor,
            role,
            now,
            commit,
            _reject,
        )

    def _operation(self, op, args, actor, role, now, commit, builder):
        write = Write(op, actor, role, now, args, [], [])
        return self._execute(write, commit, builder)[1]

    def _query_view(self, *, actor, now, role=None, as_of=None):
        self._refresh()
        self._actor(actor)
        clock = evaluation_time(self, now, as_of)
        return QueryView(self, clock, as_of)

    def ready(self, *, actor, now, role=None, as_of=None):
        """Return claimable, unblocked nodes in priority order."""
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.ready_items()}

    def blocked(self, *, actor, now, role=None, as_of=None):
        """Return blocked active/running nodes and their blocker IDs."""
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.blocked_items()}

    def queue(self, *, actor, now, role=None, as_of=None):
        """Return proposed gated nodes, oldest first."""
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.queue_items()}

    def history(self, id, *, actor, now, role=None, as_of=None):
        """Return events and introduction commits across a supersession family."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).history(
            id
        )

    def evidence(self, claim, *, actor, now, role=None, as_of=None):
        """Return a Claim's transitive evidence, excluding the Claim itself."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).evidence(
            claim
        )

    def stale(self, *, actor, now, role=None, as_of=None):
        """Return stale Claims with sorted distinct replacement-head IDs."""
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.stale_items()}

    def weakest(self, claim, *, actor, now, role=None, as_of=None):
        """Rank cited evidence by supersession, run count and held-out flag."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).weakest(
            claim
        )

    def alternatives(self, hypothesis, *, actor, now, role=None, as_of=None):
        """Return other members of all answered-question groups."""
        return self._query_view(
            actor=actor, now=now, role=role, as_of=as_of
        ).alternatives(hypothesis)

    def predictions(self, hypothesis, *, actor, now, role=None, as_of=None):
        """Return incoming tests with full sources and expected outcomes."""
        return self._query_view(
            actor=actor, now=now, role=role, as_of=as_of
        ).predictions(hypothesis)

    def matrix(self, question, *, actor, now, role=None, as_of=None):
        """Return the question's prediction matrix and discrimination warnings."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).matrix(
            question
        )

    def notes(self, id, *, actor, now, role=None, as_of=None):
        """Return Notes about a node, newest first."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).notes(id)

    def subtree(self, id, *, actor, now, role=None, as_of=None):
        """Return live descendants, progress counts and all-edge usage totals."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).subtree(
            id
        )

    def since(self, time, *, actor, now, role=None, as_of=None):
        """Return nodes/edges with timestamps strictly later than time."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).since(
            time
        )

    def where(
        self,
        *,
        actor,
        now,
        role=None,
        as_of=None,
        type=None,
        status=None,
        author=None,
        field=None,
        tag=None,
        text=None,
        over_budget=False,
    ):
        """Return nodes matching all supplied filters, in creation order."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).where(
            type=type,
            status=status,
            author=author,
            field=field,
            tag=tag,
            text=text,
            over_budget=over_budget,
        )

    def digest(self, *, actor, now, role=None, as_of=None):
        """Return counted operational groups evaluated at the read clock."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).digest()

    def show(self, id, *, actor, now, role=None, as_of=None):
        """Return an edge or a node with live relations, flags and warnings."""
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).show(id)

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

    def _before_state(self):
        """Read validation inputs from source files, never from cache state."""
        nodes = {
            path.stem: self._read(path.relative_to(self.path).as_posix())
            for path in (self.path / "graph/nodes").glob("*.yaml")
        }
        edges = {
            path.stem: self._read(path.relative_to(self.path).as_posix(), edge=True)
            for path in (self.path / "graph/edges").glob("*.yaml")
        }
        return nodes, edges

    def _validate(self, write: Write):
        """Normalize schemas, enforce core policy, then invoke profile callbacks."""
        if self.read_only:
            raise RuleViolation("read_only", "Project is read-only")
        try:
            self._git.check_clock(write.now)
            self._actor(write.actor)
            if not isinstance(write.op, str) or not write.op or "\n" in write.op:
                raise RuleViolation("schema", "Invalid operation name")
            if not isinstance(write.args, dict):
                raise RuleViolation("schema", "Write arguments must be a mapping")
            before_nodes, before_edges = self._before_state()
            validate_references(write, before_nodes)
            validate_permissions(self.profile, write, before_nodes)
            if write.op == "propose":
                for node in write.nodes:
                    self.profile.node_type(node.type)
            if write.op in {"propose", "link"}:
                for edge in write.edges:
                    if edge.id not in before_edges and edge.type in {
                        "supersedes",
                        "explains",
                        "instructs",
                    }:
                        raise RuleViolation(
                            "reserved_edge_type",
                            "Relation is reserved for its operation",
                            [edge.id],
                        )
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
                if write.op == "link":
                    validate_edge_endpoints(
                        self.profile, edge, {**before_nodes, **nodes}
                    )
                for endpoint in (edge.from_, edge.to):
                    self._identifier(endpoint)
                    if endpoint not in nodes and endpoint not in before_nodes:
                        raise RuleViolation(
                            "not_found", "Edge endpoint not found", [endpoint]
                        )
                if type(edge.retired) is not bool:
                    raise RuleViolation("schema", "Retired must be boolean", [edge.id])
                self._fields(edge, definition, edge=True)
                Edge.from_dict(edge.to_dict())
            validate_write(self.profile, write, before_nodes, before_edges)
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
            if write.op == "import":
                continue
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
        """Validate and persist a low-level candidate in one writer transaction."""
        return self._execute(write, commit)[0]

    def _execute(self, write, commit, builder=None):
        if self.read_only:
            raise RuleViolation("read_only", "Project is read-only")
        self._git.check_clock(write.now)
        paths = []
        try:
            with write_lock(self.path):
                self._git.check_clock(write.now)
                # Missing id arguments outrank a newly advanced profile pin.
                if isinstance(write.args, dict):
                    before_nodes, before_edges = self._before_state()
                    validate_references(write, before_nodes)
                self._refresh()
                change = deepcopy(write)
                result = None
                if builder is not None:
                    self._actor(change.actor)
                    validate_permissions(self.profile, change, before_nodes)
                    result = builder(self, change, before_nodes, before_edges)
                self._validate(change)
                files = self._encode(change)
                paths = [*files, *self._artifact_paths(change)]
                commit_id = self._apply_locked(change, files, paths, commit)
                record = None
                if result is not None:
                    identifier, edge = result
                    record = self._read(
                        f"graph/{'edges' if edge else 'nodes'}/{identifier}.yaml",
                        edge=edge,
                    ).to_dict()
                return commit_id, record
        except (
            TypeError,
            ValueError,
            AttributeError,
            KeyError,
            ValidationError,
        ) as exc:
            raise RuleViolation("schema", f"Invalid write: {exc}") from exc
        except (OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
            raise RuleViolation("write_failed", f"Write failed: {exc}", paths) from exc

    @staticmethod
    def _encode(change):
        files = {}
        try:
            for node in change.nodes:
                files[f"graph/nodes/{node.id}.yaml"] = dump_node(node.to_dict()).encode(
                    "utf-8"
                )
            for edge in change.edges:
                files[f"graph/edges/{edge.id}.yaml"] = dump_edge(edge.to_dict()).encode(
                    "utf-8"
                )
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            raise RuleViolation(
                "schema", f"Cannot encode graph records: {exc}"
            ) from exc
        return files

    def _apply_locked(self, change, files, paths, commit):
        """Persist an already validated change; caller owns the single flock."""
        for relative in files:
            self._safe_path(relative).parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, files)
        self.index.refresh()
        if not commit:
            return None
        commit_id = self._commit(paths, change)
        self.index.refresh()
        self._events(change, commit_id)
        return commit_id
