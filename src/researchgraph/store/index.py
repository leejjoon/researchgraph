"""Disposable, versioned SQLite projection. Files and HEAD remain authoritative.

content_hash() always reads every graph file and optional history file, then HEAD.
last_scan_seconds measures that complete phase (including HEAD lookup), not YAML,
SQLite or git history. last_rebuild_seconds includes the scan and excludes only
last_history_seconds; callers should also time the entire public Project call.
"""

import hashlib
import json
import os
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from stat import S_ISREG
from time import perf_counter

from researchgraph.model import Edge, Node, RuleViolation, format_timestamp
from researchgraph.store.hashing import HashScanner, hash_file
from researchgraph.store.history import History, record_key

PARALLEL_HASH_THRESHOLD = 5000
SCHEMA_VERSION = "2"
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE nodes (
 id TEXT PRIMARY KEY, type TEXT, status TEXT, created TEXT, priority INTEGER,
 lease_expires TEXT, claimable INTEGER, budget_tokens INTEGER,
 budget_compute_seconds INTEGER, usage_tokens INTEGER, usage_compute_seconds INTEGER,
 payload TEXT NOT NULL
);
CREATE TABLE edges (id TEXT PRIMARY KEY, type TEXT, from_id TEXT, to_id TEXT,
 retired INTEGER, payload TEXT NOT NULL, from_commit TEXT);
CREATE INDEX edge_targets ON edges(to_id,type);
CREATE TABLE approvals (node_id TEXT, ordinal INTEGER, payload TEXT, from_commit TEXT,
 PRIMARY KEY(node_id,ordinal));
CREATE TABLE artifacts (node_id TEXT, ordinal INTEGER, payload TEXT, from_commit TEXT,
 PRIMARY KEY(node_id,ordinal));
CREATE TABLE status_intervals (node_id TEXT, status TEXT, from_commit TEXT,
 from_time TEXT, to_commit TEXT, to_time TEXT);
CREATE VIEW ready AS
WITH RECURSIVE
live(child,parent) AS (
 SELECT id,id FROM nodes
 UNION
 SELECT live.child,e.to_id FROM live JOIN edges e ON e.from_id=live.parent
 WHERE e.retired=0 AND e.type IN ('part-of','tests','addresses','derived-from')
),
accounting(parent,child) AS (
 SELECT id,id FROM nodes
 UNION
 SELECT accounting.parent,e.from_id FROM accounting JOIN edges e ON e.to_id=accounting.child
 WHERE e.type IN ('part-of','tests','addresses','derived-from')
),
exhausted(id) AS (
 SELECT n.id FROM nodes n JOIN accounting a ON a.parent=n.id
 JOIN nodes d ON d.id=a.child GROUP BY n.id
 HAVING (n.budget_tokens IS NOT NULL AND SUM(COALESCE(d.usage_tokens,0))>=n.budget_tokens)
 OR (n.budget_compute_seconds IS NOT NULL AND
 SUM(COALESCE(d.usage_compute_seconds,0))>=n.budget_compute_seconds)
)
SELECT n.* FROM nodes n WHERE n.claimable=1
AND (n.status='active' OR (n.status='running' AND n.lease_expires IS NOT NULL
 AND n.lease_expires<=rg_now()))
AND NOT EXISTS (SELECT 1 FROM edges e JOIN nodes b ON b.id=e.from_id
 WHERE e.to_id=n.id AND e.type='blocks' AND e.retired=0 AND b.status!='done')
AND NOT EXISTS (SELECT 1 FROM live JOIN exhausted x ON x.id=live.parent WHERE live.child=n.id)
ORDER BY n.priority IS NULL,n.priority DESC,n.created,n.id;
"""


class Index:
    def __init__(self, project):
        self.project = project
        self.path = project.path / ".researchgraph/index.sqlite"
        self.connection = None
        self._identity = None
        self._loaded_hash = None
        self._verified_cache_token = None
        self._profile = None
        self._hash_scanner = None
        self._nodes, self._edges = {}, {}
        self._clock = format_timestamp(datetime.now(UTC).replace(microsecond=0))
        self.history = History(project._git)
        self.last_scan_seconds = self.last_rebuild_seconds = (
            self.last_history_seconds
        ) = 0.0

    def close(self):
        """Release the disposable SQLite connection and any isolated scan helpers."""
        try:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
                self._identity = None
                self._loaded_hash = None
        finally:
            if self._hash_scanner is not None:
                scanner, self._hash_scanner = self._hash_scanner, None
                scanner.close()

    def content_hash(self):
        """Real content scan, including all files under graph, history and HEAD."""
        started = perf_counter()
        digest = hashlib.sha256()
        graph = self.project.path / "graph"
        files = []
        pending = [(str(graph), "graph")] if graph.exists() else []
        while pending:
            directory, relative = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = relative + "/" + entry.name
                    if entry.is_symlink():
                        raise RuleViolation(
                            "schema", f"Symlink graph/history path: {name}"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append((entry.path, name))
                    elif entry.is_file(follow_symlinks=False):
                        files.append((name, entry.path))
        archive = self.project.path / ".researchgraph/history.jsonl"
        if archive.is_symlink():
            raise RuleViolation(
                "schema", "Symlink graph/history path: .researchgraph/history.jsonl"
            )
        if archive.is_file():
            files.append((".researchgraph/history.jsonl", str(archive)))
        files.sort()
        if len(files) >= PARALLEL_HASH_THRESHOLD:
            if self._hash_scanner is None:
                self._hash_scanner = HashScanner()
            try:
                hashes = self._hash_scanner.hash_files([path for _, path in files])
            except BaseException:
                # Report this operation's failure. A later explicit operation
                # gets a fresh helper owner, never a hidden retry or fallback.
                self._hash_scanner = None
                raise
            file_hashes = (
                hashes[offset : offset + 32] for offset in range(0, len(hashes), 32)
            )
        else:
            file_hashes = (hash_file(path) for _, path in files)
        for (relative, _), file_hash in zip(files, file_hashes):
            name = relative.encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(file_hash)
        self._scanned_head = self.project._git.head()
        digest.update(b"HEAD\0" + (self._scanned_head or "").encode())
        self.last_scan_seconds = perf_counter() - started
        return digest.hexdigest()

    def _connect(self):
        self.path = self.project._safe_path(".researchgraph/index.sqlite")
        identity = None
        if self.path.exists():
            stat = self.path.stat()
            if not S_ISREG(stat.st_mode) or stat.st_nlink != 1:
                raise RuleViolation(
                    "schema", "Index cache must be a single-linked regular file"
                )
            identity = (stat.st_dev, stat.st_ino)
        if (
            self.connection is not None
            and identity == self._identity
            and identity is not None
        ):
            try:
                self.connection.execute("SELECT 1")
                return
            except sqlite3.Error:
                pass
        if self.connection is not None:
            self.connection.close()
        self.connection = sqlite3.connect(self.path)
        self.connection.create_function("rg_now", 0, lambda: self._clock)
        stat = self.path.stat()
        self._identity = (stat.st_dev, stat.st_ino)
        self._loaded_hash = None
        self._verified_cache_token = None

    def _cache_token(self):
        # data_version observes external commits; total_changes and schema_version
        # also observe changes through our own connection. Neither replaces the
        # mandatory full source-content scan at the beginning of refresh().
        return (
            self.connection.execute("PRAGMA data_version").fetchone()[0],
            self.connection.total_changes,
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
        )

    def _projection_hash(self):
        """Check disposable rows and SQL definitions for accidental corruption."""
        digest = hashlib.sha256()
        queries = (
            (
                "schema",
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name",
            ),
            (
                "meta",
                "SELECT key,value FROM meta WHERE key!='projection_hash' ORDER BY key",
            ),
            ("nodes", "SELECT * FROM nodes ORDER BY id"),
            ("edges", "SELECT * FROM edges ORDER BY id"),
            ("approvals", "SELECT * FROM approvals ORDER BY node_id,ordinal"),
            ("artifacts", "SELECT * FROM artifacts ORDER BY node_id,ordinal"),
            (
                "intervals",
                "SELECT * FROM status_intervals ORDER BY node_id,from_time,from_commit,to_time,to_commit,status",
            ),
        )
        for name, query in queries:
            digest.update(name.encode("ascii") + b"\0")
            for row in self.connection.execute(query):
                encoded = record_key(row).encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
        return digest.hexdigest()

    def refresh(self):
        """Scan authoritative content; return whether the cache was rebuilt."""
        started = perf_counter()
        content = self.content_hash()
        profile = (
            self.project.profile_name,
            self.project.profile_version,
            self.project.profile.version,
            tuple(
                sorted(
                    (node.name, node.claimable)
                    for node in self.project.profile.node_types
                )
            ),
        )
        try:
            self._connect()
            metadata = dict(self.connection.execute("SELECT key,value FROM meta"))
            valid = (
                metadata.get("schema_version") == SCHEMA_VERSION
                and metadata.get("content_hash") == content
                and metadata.get("profile") == json.dumps(profile)
            )
            token = self._cache_token()
            if valid and token != self._verified_cache_token:
                valid = metadata.get("projection_hash") == self._projection_hash()
            if valid:
                # Check actual tables/view too, not just the metadata version.
                for table in (
                    "nodes",
                    "edges",
                    "approvals",
                    "artifacts",
                    "status_intervals",
                    "ready",
                ):
                    self.connection.execute(f"SELECT * FROM {table} LIMIT 0")
                if self._loaded_hash != content or self._profile != profile:
                    self._nodes = {
                        r[0]: Node.from_dict(json.loads(r[1]))
                        for r in self.connection.execute("SELECT id,payload FROM nodes")
                    }
                    self._edges = {
                        r[0]: Edge.from_dict(json.loads(r[1]))
                        for r in self.connection.execute("SELECT id,payload FROM edges")
                    }
                self._loaded_hash, self._profile = content, profile
                self._verified_cache_token = token
                return False
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, sqlite3.Error):
                code = getattr(exc, "sqlite_errorcode", None)
                if code is None:
                    raise
                code &= 0xFF
                if code not in (
                    sqlite3.SQLITE_ERROR,
                    sqlite3.SQLITE_SCHEMA,
                    sqlite3.SQLITE_CORRUPT,
                    sqlite3.SQLITE_NOTADB,
                ):
                    raise
                if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                    # Missing/outdated tables are not corrupt database bytes.
                    # Unlinking an empty cache races another reader's connection.
                    self.connection.close()
                    self.connection = None
                    self.path.unlink(missing_ok=True)
                    self._connect()
        nodes = [
            self.project._read(f"graph/nodes/{p.name}")
            for p in sorted((self.project.path / "graph/nodes").glob("*.yaml"))
        ]
        edges = [
            self.project._read(f"graph/edges/{p.name}", edge=True)
            for p in sorted((self.project.path / "graph/edges").glob("*.yaml"))
        ]
        intervals, provenance = self.history.project(nodes, self._scanned_head)
        self.last_history_seconds = self.history.last_walk_seconds
        # Schema is wholly owned by this cache. Recreate malformed/outdated schemas.
        with self.connection:
            # DDL and rows publish together. Readers never see a dropped/partial
            # schema, and concurrent rebuilders serialize through SQLite itself.
            self.connection.execute("BEGIN IMMEDIATE")
            objects = self.connection.execute(
                "SELECT type,name FROM sqlite_master WHERE type IN ('view','table') AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for kind, name in sorted(objects, key=lambda item: item[0] != "view"):
                self.connection.execute(
                    f'DROP {kind} "{name.replace(chr(34), chr(34) * 2)}"'
                )
            statement = ""
            for line in SCHEMA.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    self.connection.execute(statement)
                    statement = ""
            if statement.strip():
                raise ValueError("Incomplete internal index schema")
            self.connection.executemany(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        n.id,
                        n.type,
                        str(n.status),
                        format_timestamp(n.created),
                        n.priority,
                        format_timestamp(n.lease.expires_at) if n.lease else None,
                        int(self.project.profile.node_type(n.type).claimable),
                        (n.budget or {}).get("tokens"),
                        (n.budget or {}).get("compute_seconds"),
                        (n.usage or {}).get("tokens"),
                        (n.usage or {}).get("compute_seconds"),
                        json.dumps(n.to_dict(), ensure_ascii=False),
                    )
                    for n in nodes
                ],
            )
            self.connection.executemany(
                "INSERT INTO edges VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        e.id,
                        e.type,
                        e.from_,
                        e.to,
                        int(e.retired),
                        json.dumps(e.to_dict(), ensure_ascii=False),
                        self.history.edge_commits.get(e.id),
                    )
                    for e in edges
                ],
            )
            for kind in ("approvals", "artifacts"):
                self.connection.executemany(
                    f"INSERT INTO {kind} VALUES (?,?,?,?)",
                    [
                        (
                            n.id,
                            i,
                            record_key(record.to_dict()),
                            provenance.get((kind, n.id, record_key(record.to_dict()))),
                        )
                        for n in nodes
                        for i, record in enumerate(getattr(n, kind))
                    ],
                )
            self.connection.executemany(
                "INSERT INTO status_intervals VALUES (?,?,?,?,?,?)", intervals
            )
            self.connection.executemany(
                "INSERT INTO meta VALUES (?,?)",
                [
                    ("schema_version", SCHEMA_VERSION),
                    ("content_hash", content),
                    ("profile", json.dumps(profile)),
                ],
            )
            self.connection.execute(
                "INSERT INTO meta VALUES ('projection_hash',?)",
                (self._projection_hash(),),
            )
        self._verified_cache_token = self._cache_token()
        self._nodes, self._edges = {n.id: n for n in nodes}, {e.id: e for e in edges}
        self._loaded_hash, self._profile = content, profile
        self.last_rebuild_seconds = perf_counter() - started - self.last_history_seconds
        return True

    def get_node(self, id):
        try:
            return deepcopy(self._nodes[id])
        except KeyError as exc:
            raise RuleViolation(
                "not_found", f"Graph record not found: {id}", [id]
            ) from exc

    def get_nodes(self):
        return deepcopy(list(self._nodes.values()))

    def get_edges(self):
        return deepcopy(list(self._edges.values()))

    def ready(self, now):
        """Refresh and evaluate the ready SQL view using this connection's clock."""
        self.project._refresh()
        self._clock = format_timestamp(now)
        return [
            self.get_node(row[0])
            for row in self.connection.execute("SELECT id FROM ready")
        ]
