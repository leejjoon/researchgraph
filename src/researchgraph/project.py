"""File-backed projects and the shared low-level write boundary."""

import hashlib
import json
import re
import sqlite3
import subprocess
from contextlib import closing
from copy import deepcopy
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISLNK

import yaml
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from researchgraph.model import (
    Actor,
    Artifact,
    Edge,
    Node,
    RuleViolation,
    Status,
    Write,
    format_timestamp,
)
from researchgraph.operations import (
    _approve,
    _attach,
    _budget,
    _claim,
    _complete,
    _expand,
    _fail,
    _instruct,
    _link,
    _propose,
    _reject,
    _release,
    _renew,
    _retire,
    _supersede,
    _tag,
)
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
_OMITTED = object()


def _check_empty_import_destination(path):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise RuleViolation("not_empty", "Import destination must be empty")


class Project:
    """Own a file-backed graph, disposable index, and bounded hashing helpers.

    Prefer ``open``, ``init`` or ``import_`` to direct construction with the
    internal ``path, profile, version, read_only`` arguments. Use a context
    manager or call ``close`` to release connections and child processes.

    Operation/query methods require an ``Actor`` and an explicit aware datetime
    ``now``. Optional ``role=None`` declares a profile role; identities and roles
    are claims, not authentication. Writes default to ``commit=True``: the
    checkout writer lock covers validation, file changes, scoped Git commit and
    event hooks. ``commit=False`` still changes files and query results, but not
    HEAD, and calls no event hook. Writes return ordinary serialized node/edge
    mappings unless their method documents another result. ``apply`` returns a
    commit ID instead. Commits require Git user.name/user.email configuration.
    Validation raises ``RuleViolation`` without changing
    source. Inspect files and HEAD after ``write_failed``, especially if a
    post-commit hook failed; a durable commit may already exist.

    Reads freshly inspect source paths/content and HEAD before using the index;
    deleting index.sqlite changes no graph meaning. Query ``as_of=None`` means
    current state; a timestamp or commit selects historical visibility/status
    and event cutoffs without checkout. Commits resolve to committer time and
    equal-clock commits share a cutoff. Budget, usage, tags, priority and lease
    deliberately remain current. Returned query containers are eager ordinary
    mappings/lists. See spec/operations.md for frozen result shapes and ordered
    shared permission/error rules. Resource methods and get_node/get_nodes/
    get_edges do not require an operation actor or clock.
    """

    def __init__(self, path: Path, profile: Profile, version: str, read_only: bool):
        """Initialize an owner from resolved path/profile/pin and read-only state.

        This is the internal constructor; open/init/import_ perform factory
        validation and return the owned resource intended for normal callers.
        """
        self.path = path
        self.profile = profile
        self.profile_name = profile.name
        self.profile_version = version
        self.read_only = read_only
        self._closed = False
        self._import_preflight = False
        self._git = Git(path)
        self._pin_bytes = None
        self.index = Index(self)

    @classmethod
    def open(cls, path, read_only=False) -> "Project":
        """Open and refresh the project at ``path``; return an owned Project.

        ``read_only=False`` permits later graph writes; True refuses them while
        still allowing queries and archive export. No graph/Git checkout is
        performed. Missing directories raise ``project_not_found``; malformed
        layout/pins, unavailable profiles or too-new pins raise their domain
        errors. The returned owner must be closed, including after use in a
        long-lived consumer.
        """
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
        """Idempotently release this owner's index and file-hashing helpers.

        Return None; no source files or Git history change. Already-returned
        values remain usable, but further operations on this owner are invalid.
        """
        if not self._closed:
            try:
                self.index.close()
            finally:
                self._closed = True

    def __enter__(self):
        """Return this open owner; entering an already-closed owner raises ValueError."""
        if self._closed:
            raise ValueError("Project is closed")
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Close on context exit without suppressing the body's exception."""
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

    def _refresh(self, *, before_wait=None):
        """Refresh profile bytes and the disposable index without a writer lock."""
        if self._closed:
            raise ValueError("Project is closed")
        if self._import_preflight:
            # Empty prechange answers are valid only while the actual target
            # still has no source files, metadata or repository of its own.
            _check_empty_import_destination(self.path)
            return
        # The mandatory index content scan validates worktree membership while
        # reading HEAD, avoiding a second git subprocess for every read.
        for relative in (".researchgraph", "graph", "graph/nodes", "graph/edges"):
            # These are fixed internal paths, not caller input. Check each
            # component freshly once; the pin's _safe_path below still checks
            # resolved root ancestry and containment on every operation.
            try:
                mode = (self.path / relative).lstat().st_mode
            except FileNotFoundError:
                # Git does not preserve empty graph directories.
                if relative != ".researchgraph":
                    continue
                raise RuleViolation(
                    "invalid_project", f"Invalid project directory: {relative}"
                ) from None
            if S_ISLNK(mode):
                raise RuleViolation("schema", f"Symlink project path: {relative}")
            if not S_ISDIR(mode):
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
        self.index.refresh(before_wait=before_wait)

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
        """Create a project at ``path`` with installed ``profile='research'``.

        The required human ``actor`` and ``now`` author the initial ``rg: init``
        commit; role defaults to None. Return an open Project with the installed
        profile/version pinned. Existing unrelated files/history are preserved.
        ``commit=False`` creates real working-tree files without a commit or
        event; ``read_only=True`` refuses before creation. Unknown profiles,
        already-initialized paths, clock skew and invalid layout/permissions
        raise their ``RuleViolation`` rules. Close the returned owner.
        """
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

    def export(
        self,
        out,
        include_artifacts=False,
        since=None,
        *,
        actor,
        role=None,
        now,
        as_of=None,
    ):
        """Write canonical PAX/zstd bytes to ``out`` and return its resolved path.

        Return ``{'path': str}``; include_artifacts defaults to False. With
        ``since`` select changed members plus endpoint/evidence closure. Optional
        ``as_of`` limits membership only: selected graph/artifact files retain
        current working-tree bytes and selected nodes' complete histories,
        including later events/intervals. It is not historical redaction.
        Query the restored project with as_of to obtain a historical view.

        Actor/role/now follow the class query contract. Export is allowed on a
        read-only owner and never rewrites source history or creates a commit;
        only the external destination is written. Managed source/cache/artifact
        and Git-metadata destinations are rejected with ``schema``. Invalid
        selectors, missing artifact bytes and I/O failures remain visible.
        """
        from researchgraph.store.archive import encode, encode_history
        from researchgraph.validation import validate_graph

        view = self._query_view(actor=actor, role=role, now=now, as_of=as_of)
        destination = Path(out).expanduser().resolve()
        git_directory = (
            self.path / self._git.run("rev-parse", "--git-dir").stdout.strip()
        ).resolve()
        protected = [
            (self.path / name).resolve()
            for name in ("graph", ".researchgraph", "artifacts", ".git")
        ]
        protected.append(git_directory)
        common = git_directory / "commondir"
        if common.is_file():
            protected.append((git_directory / common.read_text().strip()).resolve())
        if destination == (self.path / ".gitignore").resolve() or any(
            destination.is_relative_to(path) for path in protected
        ):
            raise RuleViolation(
                "schema",
                "Export cannot overwrite project source or Git metadata",
                [str(destination)],
            )
        nodes, edges = view.nodes, view.edges
        included_nodes, included_edges = set(nodes), set(edges)
        if since is not None:
            changed = view.since(since)
            included_nodes = {
                record["id"] for record in changed if record["id"] in nodes
            }
            included_edges = {
                record["id"] for record in changed if record["id"] in edges
            }
            for identifier in included_edges:
                included_nodes.update((edges[identifier].from_, edges[identifier].to))
            outgoing = {}
            for edge in edges.values():
                if edge.type in {"supersedes", "cites", "derived-from"}:
                    outgoing.setdefault(edge.from_, []).append(edge)
            pending = list(included_nodes)
            visited = set()
            while pending:
                identifier = pending.pop()
                if identifier in visited:
                    continue
                visited.add(identifier)
                for edge in outgoing.get(identifier, ()):
                    included_edges.add(edge.id)
                    included_nodes.add(edge.to)
                    pending.append(edge.to)
        selected_nodes = {
            identifier: view.source.nodes[identifier] for identifier in included_nodes
        }
        selected_edges = {
            identifier: view.source.edges[identifier] for identifier in included_edges
        }
        # As-of governs membership, never the stored payload (decision 015).
        validate_graph(self.profile, selected_nodes, selected_edges)
        files = {
            ".researchgraph/profile.yaml": self._safe_path(
                ".researchgraph/profile.yaml"
            ).read_bytes()
        }
        rows = [row for row in view.intervals if row["node_id"] in included_nodes]
        files[".researchgraph/history.jsonl"] = encode_history(rows)
        for kind, records in (("nodes", selected_nodes), ("edges", selected_edges)):
            for identifier in records:
                relative = f"graph/{kind}/{identifier}.yaml"
                files[relative] = self._safe_path(relative).read_bytes()
        directories = {".researchgraph/", "graph/", "graph/nodes/", "graph/edges/"}
        if include_artifacts:
            directories.add("artifacts/")
            artifact_root = self._safe_path("artifacts")
            if artifact_root.exists():
                for path in artifact_root.rglob("*"):
                    relative = path.relative_to(self.path).as_posix()
                    self._safe_path(relative)
                    if (
                        (since is not None or as_of is not None)
                        and len(PurePosixPath(relative).parts) > 1
                        and PurePosixPath(relative).parts[1] not in included_nodes
                    ):
                        continue
                    if path.is_dir():
                        directories.add(relative + "/")
                    elif path.is_file():
                        files[relative] = path.read_bytes()
                    else:
                        raise RuleViolation(
                            "schema", "Artifacts must be regular files", [relative]
                        )
        content = encode(files, directories)
        try:
            atomic_write(destination.parent, {destination.name: content})
        except OSError as exc:
            raise RuleViolation(
                "write_failed", f"Export failed: {exc}", [str(destination)]
            ) from exc
        return {"path": str(destination)}

    @classmethod
    def import_(
        cls,
        archive,
        path,
        *,
        actor,
        role=None,
        now,
        commit=True,
        read_only=False,
    ) -> "Project":
        """Restore ``archive`` into empty/nonexistent ``path``; return its owner.

        Human actor and now are required; role=None, commit=True and
        read_only=False are defaults. Validate encoding, profile, graph and all
        restored timestamps before the core creates destination files or its
        independent Git repository, even inside another checkout. Now must be
        at least the import clock floor, including lease expiry and complete
        history; equality is allowed. Earlier times raise ``clock_skew``.

        Preserve graph bytes, timestamps and interval provenance. A committed
        restore makes exactly one ``rg: import`` commit, not a separate init;
        approval/artifact/supersedes introduction commits become that commit.
        ``commit=False`` leaves a real pending restore without events. Nonempty
        destinations, malformed archives, unavailable/newer profiles and
        permission failures raise their domain rules. Close the returned owner.
        """
        from researchgraph.store.archive import read
        from researchgraph.store.index import SCHEMA

        if read_only:
            raise RuleViolation("read_only", "Project is read-only")
        cls._actor(actor)
        try:
            format_timestamp(now)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuleViolation("schema", f"Invalid write time: {exc}") from exc
        files, directories, profile, version, nodes, edges, _, floor = read(archive)
        if floor is not None and now < floor:
            raise RuleViolation(
                "clock_skew", "Import time is earlier than restored timestamps"
            )
        change = Write("import", actor, role, now, {}, nodes, edges)
        validate_permissions(profile, change, {})
        root = Path(path).expanduser().resolve()
        _check_empty_import_destination(root)
        project = cls(root, profile, version, False)
        try:
            project._import_preflight = True
            try:
                # Ordinary validator reads use the same empty index schema,
                # owned and closed by Index, but never create a destination DB.
                project.index.connection = sqlite3.connect(":memory:")
                with closing(project.index.connection.cursor()) as cursor:
                    cursor.executescript(SCHEMA)
                project.index.connection.create_function(
                    "rg_now", 0, lambda: format_timestamp(now)
                )
                project.index._scanned_head = None
                project.index._compact_models()
                for relative in [*files, *directories]:
                    project._safe_path(relative)
                project._validate(change, restoring=True)
            except RuleViolation as exc:
                if exc.rule == "not_empty":
                    raise
                raise RuleViolation(
                    "bad_archive", f"Invalid restored graph: {exc}", exc.ids
                ) from exc
            finally:
                project._import_preflight = False
                project.index.close()
            _check_empty_import_destination(root)
            root.mkdir(parents=True, exist_ok=True)
            # Import must not inherit an unrelated ancestor repository or HEAD.
            metadata = project._safe_path(".researchgraph")
            metadata.mkdir(exist_ok=True)
            with write_lock(root):
                if any(p.name != ".researchgraph" for p in root.iterdir()) or any(
                    p.name != "lock" for p in (root / ".researchgraph").iterdir()
                ):
                    raise RuleViolation(
                        "not_empty", "Import destination is no longer empty"
                    )
                project._git.run("init", str(root))
                for relative in directories | {
                    "graph",
                    "graph/nodes",
                    "graph/edges",
                    "artifacts",
                }:
                    project._safe_path(relative).mkdir(parents=True, exist_ok=True)
                restored = {
                    **files,
                    ".gitignore": b".researchgraph/index.sqlite\n.researchgraph/lock\n",
                }
                for relative in restored:
                    project._safe_path(relative).parent.mkdir(
                        parents=True, exist_ok=True
                    )
                atomic_write(root, restored)
                project._pin_bytes = files[".researchgraph/profile.yaml"]
                project.index.refresh()
                if commit:
                    commit_id = project._commit(list(restored), change)
                    project.index.refresh()
                    project._events(change, commit_id)
            return project
        except (OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
            project.close()
            raise RuleViolation(
                "write_failed", f"Import failed: {exc}", list(files)
            ) from exc
        except BaseException:
            project.close()
            raise

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
        """Return the current Node model for canonical ``id`` after freshness checks.

        This low-level getter has no actor/clock/as_of arguments. Invalid IDs
        raise ``schema`` and absent nodes raise ``not_found``. Source is not
        changed, although the disposable index may be rebuilt.
        """
        self._refresh()
        self._identifier(id)
        return self.index.get_node(id)

    def get_nodes(self) -> list[Node]:
        """Return current Node models of every type/status, including retired ones.

        There are no filters or context arguments; use where/show for public
        contextual queries. Source stays unchanged; refreshing may rebuild the
        disposable index and source/layout errors propagate.
        """
        self._refresh()
        return self.index.get_nodes()

    def get_edges(self) -> list[Edge]:
        """Return all current Edge models, including retired stored relations.

        No actor/clock or filter is accepted. Fresh source checks may rebuild
        the disposable index, but do not change graph files or Git history;
        source/layout errors remain visible to the caller.
        """
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
        """Create ``type``/``title`` and return the new serialized node.

        Body defaults to empty text; fields=None and edges=None mean empty
        inputs. Root-property defaults are copied into fields before complete
        profile validation. Each incident edge names type and one existing
        from/to endpoint, with optional fields. Gates determine initial status.
        Unknown types, reserved edges, missing/invalid endpoints, duplicate
        relations, cycles and invalid fields raise their domain rules before
        source changes. Shared actor/role/now/commit behavior is on Project.
        """
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
        """Create ``type`` from node ``from_`` to ``to``; return its edge mapping.

        Fields default to an empty mapping and receive profile defaults. Both
        endpoints must exist (retired nodes are permitted). Reserved types,
        invalid endpoint pairs/fields, duplicate live relations and constrained
        cycles raise their specific domain rules. The shared write contract
        controls permission, timestamps, scoped commit and event effects.
        """
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
        """Approve proposed ``node`` and return its revised serialized record.

        What defaults to 'activate', the only supported value; note=None adds
        no note text. Record the approval and activate once every matching gate
        is satisfied. Gates determine whether the actor/declared role may
        approve; not_proposed, human_only_approval, wrong_role, already_approved
        or unknown_what are explicit failures. Shared write effects apply.
        """
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
        """Human-reject proposed ``node`` with ``reason``; return the retired node.

        Record rejection and retire all live incident edges in the same write.
        An existing non-proposal raises not_proposed; shared missing-ID,
        permission and transactional write rules apply.
        """
        return self._operation(
            "reject",
            {"node": node, "reason": reason},
            actor,
            role,
            now,
            commit,
            _reject,
        )

    def supersede(
        self,
        node,
        title=None,
        body=None,
        fields=None,
        inherit_edges=True,
        instruction=None,
        *,
        actor,
        role=None,
        now,
        commit=True,
    ):
        """Supersede ``node`` and return its new same-type replacement mapping.

        None title/body/fields preserve the old values; supplied fields replace
        the complete mapping and receive creation defaults. Retire the old node
        and transferable edges; inherit_edges=True recreates those edges using
        their per-type inheritance policy, including inherited explains.
        Nontransferable edges stay untouched. Optional instruction creates an
        Instruction for the replacement. Its gates reset initial approval.

        Humans may replace any type; agents only Task/Result. Running or already
        retired targets, invalid fields and cycles raise their domain rules.
        Shared actor/role/now/commit behavior applies to the whole write.
        """
        return self._operation(
            "supersede",
            {
                "node": node,
                "title": title,
                "body": body,
                "fields": fields,
                "inherit_edges": inherit_edges,
                "instruction": instruction,
            },
            actor,
            role,
            now,
            commit,
            _supersede,
        )

    def instruct(self, node, text, *, actor, role=None, now, commit=True):
        """Create an Instruction containing ``text`` for ``node``; return it.

        The human-only write adds an instructs edge to a proposed/active target.
        Other target states raise already_ran; missing IDs, profile support and
        shared permissions/commit behavior follow the Project write contract.
        """
        return self._operation(
            "instruct",
            {"node": node, "text": text},
            actor,
            role,
            now,
            commit,
            _instruct,
        )

    def retire(self, node, reason, *, actor, role=None, now, commit=True):
        """Human-retire ``node`` with ``reason`` and return its revised mapping.

        Proposed, active and failed nodes are eligible; retire their live
        incident edges too. Running targets raise running, while done/retired
        targets raise terminal_status. No replacement is created. Shared
        validation, scoped commit and event behavior applies.
        """
        return self._operation(
            "retire",
            {"node": node, "reason": reason},
            actor,
            role,
            now,
            commit,
            _retire,
        )

    def claim(self, node, lease_seconds=7200, *, actor, role=None, now, commit=True):
        """Claim ready ``node`` for this actor; return its running node mapping.

        Lease_seconds defaults to 7200 and must be a positive integer. An
        expired running lease may be replaced; record claim with acquired_at
        now and expires_at now+lease_seconds. An unexpired lease raises
        lease_held before not_ready; blocked/status/budget exclusions raise
        not_ready and invalid duration raises negative/schema. Shared write
        rules apply; this acquires a lease, not an authenticated identity.
        """
        return self._operation(
            "claim",
            {"node": node, "lease_seconds": lease_seconds},
            actor,
            role,
            now,
            commit,
            _claim,
        )

    def renew(self, node, lease_seconds=7200, *, actor, role=None, now, commit=True):
        """Renew held ``node`` for lease_seconds=7200; return the running record.

        Set expiry to max(current expiry, now+duration), never shortening it or
        changing the holder. Renewal is allowed after expiry by that holder.
        Non-running nodes, another actor and nonpositive/invalid durations raise
        not_running, not_lease_holder or negative/schema. Record renewal using
        the shared transactional write and commit policy.
        """
        return self._operation(
            "renew",
            {"node": node, "lease_seconds": lease_seconds},
            actor,
            role,
            now,
            commit,
            _renew,
        )

    def release(
        self, node, note=None, usage=None, *, actor, role=None, now, commit=True
    ):
        """Release running ``node`` to active and return its revised mapping.

        The holder or any human may release it, clearing the lease. Optional
        note defaults to None; optional usage adds nonnegative integer tokens/
        compute_seconds, never replaces prior totals. Non-running or disallowed
        holders and invalid usage raise their domain rules. Shared scoped
        commit/event behavior applies; the core does not meter work itself.
        """
        return self._operation(
            "release",
            {"node": node, "note": note, "usage": usage},
            actor,
            role,
            now,
            commit,
            _release,
        )

    def complete(
        self,
        node,
        summary,
        artifacts=None,
        usage=None,
        *,
        actor,
        role=None,
        now,
        commit=True,
    ):
        """Complete held ``node`` with ``summary``; return its done node mapping.

        Clear the lease and append completion. Artifacts=None names no files;
        each supplied path must already be a regular, nonsymlink owned file
        under artifacts/<node>/ relative to the root. Append its actual SHA-256,
        byte count and actor/time record; stage only these named artifacts with
        the graph write. Usage=None adds nothing; supplied counters are additive.
        Wrong status/holder, unsafe or missing paths and invalid counters raise
        their domain rules before publication. Shared commit behavior applies.
        """
        return self._operation(
            "complete",
            {"node": node, "summary": summary, "artifacts": artifacts, "usage": usage},
            actor,
            role,
            now,
            commit,
            _complete,
        )

    def fail(
        self,
        node,
        summary,
        step=None,
        check=None,
        measured=None,
        error=None,
        usage=None,
        *,
        actor,
        role=None,
        now,
        commit=True,
    ):
        """Fail held ``node`` with ``summary``; return its failed node mapping.

        Clear the lease, append fail and create a profile-initialized Failure
        plus explains edge. Optional step/check/measured/error populate Failure
        fields when supplied; usage=None adds nothing, otherwise counters add
        to stored totals. The operation does not execute or retry anything.
        Non-running/wrong-holder and invalid usage/fields raise their domain
        rules. Shared actor/role/now/commit behavior covers the whole write.
        """
        return self._operation(
            "fail",
            {
                "node": node,
                "summary": summary,
                "step": step,
                "check": check,
                "measured": measured,
                "error": error,
                "usage": usage,
            },
            actor,
            role,
            now,
            commit,
            _fail,
        )

    def expand(self, nodes, hint=None, *, actor, role=None, now, commit=True):
        """Human-create one expansion Task for ``nodes`` and return its mapping.

        Preserve the nonempty target-ID order; hint=None supplies no expansion
        description. The profile must support the reserved expansion fields.
        Empty targets raise no_targets; missing targets/profile support raise
        their shared rules. This only records a Task: expansion_hook is never
        called. Gates and the shared write/commit contract still apply.
        """
        return self._operation(
            "expand",
            {"nodes": nodes, "hint": hint},
            actor,
            role,
            now,
            commit,
            _expand,
        )

    def budget(
        self,
        node,
        tokens=None,
        compute_seconds=None,
        *,
        actor,
        role=None,
        now,
        commit=True,
    ):
        """Human-replace ``node``'s budget and return the revised node mapping.

        Tokens and compute_seconds default to None: omitted dimensions are
        removed, and both None clear the budget. Nonnegative integer zero is
        inclusive (stops readiness); negatives/invalid scalars raise negative/
        schema. Record the change. Subtree/ancestor accounting is enforced by
        ready queries, not metering or a write-execution scheduler.
        """
        return self._operation(
            "budget",
            {"node": node, "tokens": tokens, "compute_seconds": compute_seconds},
            actor,
            role,
            now,
            commit,
            _budget,
        )

    def tag(
        self,
        node,
        add=None,
        remove=None,
        priority=_OMITTED,
        *,
        actor,
        role=None,
        now,
        commit=True,
    ):
        """Edit ``node``'s sorted unique tags/priority; return the revised mapping.

        Add/remove default to None (no label edits). Omitted priority preserves
        it, explicit None clears it, and an integer sets it. Invalid labels or
        priority raise schema. No approval/history event record is appended;
        the normal event hook still runs after a committed write, never for
        commit=False. Shared permission and scoped file/commit rules apply.
        """
        args = {"node": node, "add": add, "remove": remove}
        if priority is not _OMITTED:
            args["priority"] = priority
        return self._operation("tag", args, actor, role, now, commit, _tag)

    def attach(self, node, artifacts, *, actor, role=None, now, commit=True):
        """Attach existing ``artifacts`` to ``node``; return the revised mapping.

        For each owned relative regular nonsymlink path, append actual SHA-256,
        byte count and actor/time. Repeated paths retain repeated records.
        Commit stages only named files; their bytes are not moved or rewritten.
        Every status except retired is permitted; retired, unsafe and missing
        paths raise terminal_status, artifact_path or artifact_missing. Shared
        transactional write and event behavior applies to the entire batch.
        """
        return self._operation(
            "attach",
            {"node": node, "artifacts": artifacts},
            actor,
            role,
            now,
            commit,
            _attach,
        )

    def _operation(self, op, args, actor, role, now, commit, builder):
        write = Write(op, actor, role, now, args, [], [])
        return self._execute(write, commit, builder)[1]

    def _query_view(self, *, actor, now, role=None, as_of=None, before_wait=None):
        self._refresh(before_wait=before_wait)
        self._actor(actor)
        clock = evaluation_time(self, now, as_of)
        return QueryView(self, clock, as_of)

    def ready(self, *, actor, now, role=None, as_of=None):
        """Return ``{'items': [...]}`` of ready nodes, highest priority first.

        Require a claimable type, eligible status/expired lease, no live blocker
        and no inclusive self/ancestor budget exclusion. Ties use creation time
        then ID; null priority sorts last. Actor/now/role/as_of use the class
        query contract; no claim, scheduling or source write occurs.
        """
        source = self.index._query_source
        prepared = None

        def prepare():
            nonlocal prepared
            prepared = source, source.clone_ready_batch()

        view = self._query_view(
            actor=actor,
            now=now,
            role=role,
            as_of=as_of,
            before_wait=prepare if source is not None and source.ready_ranks else None,
        )
        return {"items": view.ready_items(prepared)}

    def blocked(self, *, actor, now, role=None, as_of=None):
        """Return blocked active/running nodes as an items envelope.

        Each node adds blocking IDs; ordering matches ready priority order.
        Blockers and statuses honor as_of under the partial historical contract
        on Project. This is a read with no lease or graph mutation.
        """
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.blocked_items()}

    def queue(self, *, actor, now, role=None, as_of=None):
        """Return proposed gated nodes in an items envelope, oldest first.

        Rows include gate/role metadata and approvals so far. Only types with a
        matching activate gate enter the queue. Actor/now/role/as_of and source
        freshness follow the shared read contract; no approval is performed.
        """
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.queue_items()}

    def history(self, id, *, actor, now, role=None, as_of=None):
        """Return ``id``'s supersession-family history as an oldest-first list.

        Include supersedes edges (retired too), approvals, artifacts and status
        intervals with kind/node/commit/at metadata. Missing nodes raise
        not_found; as_of applies the shared temporal cutoff. Imported approval,
        artifact and supersedes introductions report the import commit;
        archived interval commit IDs retain their recorded provenance.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).history(
            id
        )

    def evidence(self, claim, *, actor, now, role=None, as_of=None):
        """Return ``claim``'s evidence as ``{'items': [...]}``, excluding itself.

        Traverse live cites/derived-from closure and include evidence artifacts.
        Missing target IDs raise not_found. Actor/now/role/as_of use the common
        read contract, including historical visibility/event filtering and
        current mutable metadata. No graph or artifact bytes are changed.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).evidence(
            claim
        )

    def stale(self, *, actor, now, role=None, as_of=None):
        """Return stale Claims in an items envelope, ordered by creation/ID.

        Every row's responsible value is a sorted distinct array of all
        replacement-head IDs, not a scalar or one path's answer. Resolve heads
        and evidence paths using edges effective at as_of. Shared query context
        and partial mutable-field history rules apply; no replacements occur.
        """
        view = self._query_view(actor=actor, now=now, role=role, as_of=as_of)
        return {"items": view.stale_items()}

    def weakest(self, claim, *, actor, now, role=None, as_of=None):
        """Return ``claim``'s cites targets as a weakest-first evidence list.

        Sort superseded-input evidence first, then runs (missing=0), held_out
        (missing=False) and ID. Missing target IDs raise not_found. This query
        ranks records only; actor/now/role/as_of and freshness follow Project.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).weakest(
            claim
        )

    def alternatives(self, hypothesis, *, actor, now, role=None, as_of=None):
        """Return other hypotheses from every question answered by ``hypothesis``.

        Deduplicate the union in creation order and include status plus live
        supporting/contradicting Claims. Missing IDs raise not_found; apply the
        shared actor/clock/as_of contract without changing exclusion groups.
        """
        return self._query_view(
            actor=actor, now=now, role=role, as_of=as_of
        ).alternatives(hypothesis)

    def predictions(self, hypothesis, *, actor, now, role=None, as_of=None):
        """Return live incoming tests for ``hypothesis`` as oldest-first rows.

        Rows carry the full source record, expected outcome and discriminating
        information, including source status. Missing IDs raise not_found.
        Shared context/as_of selects effective nodes/edges; no test is executed.
        """
        return self._query_view(
            actor=actor, now=now, role=role, as_of=as_of
        ).predictions(hypothesis)

    def matrix(self, question, *, actor, now, role=None, as_of=None):
        """Return ``question``'s ``{'rows': [...], 'columns': [...]}`` matrix.

        Sources and exclusion-group hypotheses use creation order. Cells map
        hypothesis IDs to expected outcomes/empty cells; each row includes its
        global non_discriminating warning. Missing IDs raise not_found. Apply
        the shared temporal read contract without running predictions.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).matrix(
            question
        )

    def notes(self, id, *, actor, now, role=None, as_of=None):
        """Return Notes with a live about edge to ``id``, newest first.

        Missing target nodes raise not_found. Actor/now/role/as_of use the class
        read contract; only visibility is selected, and no Note is created.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).notes(id)

    def subtree(self, id, *, actor, now, role=None, as_of=None):
        """Return ``id``'s nodes/status_counts/usage subtree mapping.

        Progress uses live containment descendants; usage accounting includes
        retired work through stored part-of relations and deduplicates diamonds.
        Missing IDs raise not_found. Shared historical context applies, while
        usage remains deliberately current; no budget or status is modified.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).subtree(
            id
        )

    def since(self, time, *, actor, now, role=None, as_of=None):
        """Return node/edge mappings changed strictly after RFC3339 ``time``.

        Consider creation/update, approvals, artifact additions and edge
        retirement timestamps, not just node.updated. An as_of cutoff limits
        the changed set to that view; invalid times/selectors raise their domain
        rules. Actor/now/role follow the shared read contract; source is unchanged.
        """
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
        """Return a creation-ordered node list matching every supplied filter.

        None type/status/author/field/tag/text means no corresponding filter;
        field maps names to string comparisons, author matches actor name and
        text searches title/body case-insensitively. Over_budget=False adds no
        budget filter; True uses inclusive current subtree accounting. Filters
        conjoin. Actor/now/role/as_of use the class read contract, including
        current tags/budgets/usage rather than invented historical values.
        """
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
        """Return an items envelope of counted operational node groups.

        Include running, ready, blocked, awaiting, failed_24h and non-retired
        Claims, with their lists/counts. The supplied now/as_of read context
        determines predicates and the failure window; mutable metadata remains
        current under Project's temporal contract. Nothing is scheduled.
        """
        return self._query_view(actor=actor, now=now, role=role, as_of=as_of).digest()

    def show(self, id, *, actor, now, role=None, as_of=None):
        """Return ``id``'s edge mapping or enriched node mapping.

        Nodes add live incident edges, instructions, pins, derived flags and
        profile warnings. Missing IDs raise not_found. Apply actor/now/role/
        as_of through the common partial historical view; this does not check
        out files, execute instructions or alter the node.
        """
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

    def _validate(self, write: Write, *, restoring=False):
        """Normalize schemas, enforce core policy, then invoke profile callbacks."""
        if self.read_only:
            raise RuleViolation("read_only", "Project is read-only")
        try:
            if not restoring:
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
                self._fields(node, definition, edge=False, restoring=restoring)
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
                self._fields(edge, definition, edge=True, restoring=restoring)
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

    def _fields(self, record, definition, *, edge, restoring=False):
        path = self.path / f"graph/{'edges' if edge else 'nodes'}/{record.id}.yaml"
        if not restoring and not path.exists():
            record.fields = self.profile.normalize_fields(
                record.type, record.fields, edge=edge
            )
        else:
            # Never materialise newly installed defaults into existing records.
            if not isinstance(record.fields, dict):
                raise RuleViolation("schema", "Fields must be a mapping", [record.id])
            validator_for(definition.schema)(definition.schema).validate(record.fields)
        # Cache/CLI serialization cannot reject data after source files change.
        json.dumps(record.fields)

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

    def _checked_artifact_paths(self, paths, node_id=None):
        """Validate the whole path batch before testing any file's existence."""
        if paths is None:
            paths = []
        if not isinstance(paths, list):
            raise RuleViolation("schema", "Artifacts must be a list of paths")
        checked = [
            (relative, self._artifact_path(relative, node_id)) for relative in paths
        ]
        for relative, path in checked:
            if not path.is_file():
                raise RuleViolation(
                    "artifact_missing", f"Artifact not found: {relative}", [relative]
                )
        return checked

    def _artifact_records(self, paths, node_id, actor, now):
        """Collect complete/attach records without writing or staging files.

        Paths are relative strings belonging to node_id. Every path is checked
        before any missing-file error; every file exists before hashing starts.
        Duplicate paths produce duplicate append-only records. Hash and size
        describe the entire stream, using bounded memory.
        """
        records = []
        for relative, path in self._checked_artifact_paths(paths, node_id):
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            records.append(Artifact(relative, digest.hexdigest(), size, now, actor))
        return records

    def _artifact_paths(self, write):
        paths = write.args.get("artifacts")
        if paths is None:
            paths = []
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
        self._checked_artifact_paths(paths)
        return list(dict.fromkeys(paths))

    def _commit(self, paths, write):
        identifier = write.args.get("node")
        if identifier is None and write.op not in {"init", "import"}:
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
        """Validate/persist candidate ``Write``; return commit ID or None.

        Prefer named operations for normal use. This low-level boundary copies
        and validates the supplied nodes/edges against freshly read state under
        the checkout writer lock, then serializes the candidate. Commit=True
        stages only its changed/named files and emits post-commit hooks;
        commit=False still writes files/index-visible state without a commit.
        Shared permission, schema, invariant and write_failed rules apply.
        """
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
