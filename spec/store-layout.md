# Store layout and index (normative)

## Files
```
<project>/
  .researchgraph/
    profile.yaml        # {name: research, version: "1.0"}
    history.jsonl       # status intervals, present only in projects created by import (see Index)
    index.sqlite        # gitignored, disposable
    lock                # gitignored, flock target for writers
  graph/
    nodes/<id>.yaml     # one node
    edges/<id>.yaml     # one edge
  artifacts/<node id>/  # files attached to that node (optional data-versioning layer may manage large ones)
  .gitignore            # includes .researchgraph/index.sqlite and .researchgraph/lock
```
`rg init` creates this layout and the first commit. `profile.yaml` is exactly `name: <name>\nversion: "<version>"\n`. `.gitignore` is created if absent, else the two lines are appended.

## File format
Serialised with PyYAML (YAML 1.1 semantics) in this **canonical form** (decision 011). A no-op rewrite produces no diff.

- UTF-8, LF line endings, one trailing newline, no document markers, no comments, no anchors or aliases, no tags.
- 2-space indent; block style for every mapping and sequence; empty mapping as `{}` and empty sequence as `[]`.
- Top-level keys always present, in the order of the tables in `data-model.md` §2 and §3, with `null` written for absent optional values. Inside `actor` mappings, `approvals[]`, `artifacts[]`, `budget`, `usage` and `lease`: keys in the order given in `data-model.md`; keys with no value omitted. Inside `fields` (node and edge) and every mapping nested in it: keys in sorted codepoint order, whatever order the caller or schema gave them.
- Sequences are written with PyYAML's default indentation (items at the parent key's indent, `- ` prefixed). Floats are written as PyYAML's float representer does: Python `repr` lower-cased, with `.0` inserted before the exponent when the mantissa has no dot (`1.0e-06`, never `1e-06`), and `.inf`, `-.inf`, `.nan` for the specials. Integers must not be written as floats. A float fixture is required in `tests/fixtures/canonical/`.
- When a multi-line string cannot use the literal block style (a line with trailing spaces, or a carriage return), it is double-quoted with escapes.
- Strings: plain when PyYAML would read them back unchanged as a string; otherwise double-quoted with escapes (this covers `yes`, `no`, `on`, `off`, `null`, `~`, numbers such as `1.0` and `012`, strings with leading or trailing spaces, and empty strings). Strings containing a newline use the literal block style `|` with the `-` or `+` chomping indicator needed to round-trip trailing newlines. No line wrapping (`width` unlimited). Non-ASCII written as-is (`allow_unicode`).
- Integers in decimal; booleans `true`/`false`; `null` as `null`.
- Timestamps RFC 3339 UTC with whole seconds and `Z` (`2026-10-19T14:06:19Z`), written as quoted strings so YAML 1.1 does not parse them.
- One file per node or edge, named `<id>.yaml`. No subdirectories in v0.1 (sharding is a reserved future change: `graph/nodes/<first two chars>/<id>.yaml`).

Fixtures for every canonical-form rule live in `tests/fixtures/canonical/` and the round-trip property test (T02) uses them.

## Writes
A write is one transaction:
1. Take an exclusive `flock` on `.researchgraph/lock` for the whole write. A second writer in the same checkout waits; a `--read-only` invocation takes no lock.
2. Refresh the index if stale (see Index).
3. Validate the proposed change against the profile and invariants using the index. Any failure returns `RuleViolation` and touches nothing.
4. Write every node and edge file of the change: each to a temp file in the same directory then `rename`. If any rename fails, restore every file already renamed to the bytes it had before this write (kept in memory; a file that did not exist is deleted) and raise `write_failed`. Restoring from `HEAD` is wrong because an earlier `--no-commit` write may be pending.
5. Update the index.
6. If `--commit` (CLI default: on) stage exactly the touched files, plus artifact files named by `complete`, `attach` or `import`, and commit with message `rg: <operation> <id>` (`operations.md`). The commit author is the human's git identity (`user.name <user.email>`), or `NAME <NAME@agents.researchgraph>` for an agent actor. The commit's author and committer dates are both `now`, so git history and stored timestamps share one clock. Pre-existing staged or unstaged changes to other files are left alone. If the commit fails, the files stay written and `write_failed` is raised with the paths.
7. Call the profile's event hooks (only after a commit).

Concurrency across clones is not coordinated: two claims on one node in different clones both succeed locally and the second to push loses the merge and must re-read. Within one checkout the lock makes claims exclusive.

## Index
SQLite at `.researchgraph/index.sqlite`, schema version in a `meta` table. Tables: `nodes`, `edges`, `approvals`, `artifacts`, `status_intervals`, `meta`. A `ready` view implements `data-model.md` §6 given `now`.

- Staleness: `meta.content_hash` is a hash over the sorted list of `(path, sha256 of content)` for every file under `graph/` and for `.researchgraph/history.jsonl` when present, plus the `HEAD` commit id. It is checked on open and again at the start of every operation on a long-lived `Project`. Mismatch → rebuild from files. Working-tree edits and `--no-commit` writes therefore invalidate the index.
- Rebuild must complete in < 5 s for 20,000 nodes on a laptop, excluding the git log walk, which is measured separately (measured in CI with a synthetic project).
- **Deleting the index must never change the result of any query.** CI runs the acceptance suite twice: once with the index kept, once with it deleted before every command.

### Change events (decision 020)

A ChangeSource polls persisted state and evaluation time, comparing observations rather than relying on in-process hooks or index rebuilds. The source fingerprint (`content_hash`, including graph/history bytes and HEAD) is not an event generation. Each instance mints an independent random 32-lowercase-hex generation and monotonically increasing sequence numbers. Every envelope carries `cursor: "v1:<generation>:<seq>"`, `seq`, `generation`, `at` (observation time) and `commit` (observed HEAD, not causal provenance).

Envelope kinds are:
- `index`: `{cursor, seq, generation, at, commit, kind: index, counts: {nodes, edges}, reset?}`; counts include retired records.
- `node`: `{cursor, seq, generation, at, commit, kind: node, change: created|updated, id, previous_status, node}`; previous_status is null for creation, otherwise the prior observed stored status. Node is the complete persisted record.
- `edge`: `{cursor, seq, generation, at, commit, kind: edge, change: created|retired, id, edge}`; edge is the complete persisted record.

An observation emits node/edge deltas followed by an index envelope when persisted state, HEAD or time-derived predicates change. Clock-only lease expiry emits an index notification without rewriting running nodes or inventing a status transition. Unchanged observations and cache rebuilds alone fabricate no domain events. Same-second writes are detected by state, not timestamps alone. An already-refreshed cache must not hide a change. External processes, no-commit writes, retained bytes after commit failure and post-commit hook failures all converge on persisted state. Validation failures and completely rolled-back failures emit no domain changes (independent clock/HEAD changes may still notify).

Retain a bounded in-memory window; consumers may see coalesced state differences between polls, not every intermediate write. A retained cursor resumes strictly after its envelope. Malformed, unknown/future, expired, prior-generation or restarted-instance cursors produce one index reset (`reset: true`) instead of partial deltas. This recovery reply is a detached copy of the latest retained index, with its associated cursor/sequence/observation time. It does not append to the window or advance the source sequence: recovery by one consumer must not evict another consumer's current cursor, including at retention one (decision 024). A disappeared record instead emits a new shared index reset because deltas cannot express deletion. Repeated unchanged bytes after restart do not validate an old cursor. Slow consumers falling outside retention reset; disconnected owners are closed. No persistent event file and no durable replay guarantee.

Project.changes(actor, now, role?, since?) returns `{events: [...], cursor: ...}` with this observation's envelopes, or strictly the retained cursor's successors when since is supplied. Its lazily owned ChangeSource closes with the Project. An explicitly borrowed ChangeSource supports snapshot returning `{graph: {nodes: [...], edges: [...]}, cursor: ...}` from one observation; closing it releases retained state, not its borrowed Project. Calls to one source are serialized by the caller. Snapshot followed by subscription/catch-up from its associated cursor must converge; clients receiving reset refetch graph/digest/ready/blocked/running and profile. Do not subscribe from an unrelated cursor after fetching a snapshot and assume no gap. Applying deltas to the first graph, replacing state on reset, yields a fresh graph at the same observation. Deleting the index preserves state equality, not cursor, generation or observation timestamps. CLI since and SSE id/Last-Event-ID carry the identical complete cursor.

### status_intervals
`(node_id, status, from_commit, from_time, to_commit, to_time)`, `to_*` null for the newest interval of each node. Every new interval for a node, whether git-derived or the uncommitted open interval, closes that node's previous open interval at its own `(from_commit, from_time)`, so a node never has two open intervals. A new uncommitted interval replaces a previous uncommitted one for the same node rather than closing it, because a rebuild from files sees only `HEAD` plus the working tree, and rebuild and incremental extension must agree. Built by walking `git log --reverse -- graph/nodes` once on first open, then extended per new commit; `from_time` of a git-derived interval is the commit's committer time (which Writes step 6 sets to `now`). The same walk records, for every `approvals[]` and `artifacts[]` entry and for every edge file, the commit that introduced it (null while uncommitted), which `history` reports for approval, artifact and `supersedes` records. In a project created by `import` that commit is `rg: import` for everything restored from the archive: the archive carries files, not their commits (decisions 008 and 015), and only status intervals keep their commit ids, through `history.jsonl`. Uncommitted state is an open interval with `from_commit` null and `from_time` = the node's `updated`. The rows are the union of git-derived intervals and `.researchgraph/history.jsonl` when that file exists (one JSON object per line with the six keys in that order, times as RFC 3339 strings, open `to_*` as JSON `null`, no spaces after separators, lines sorted by `(node_id, from_time, status)`; generated by `export` into the archive only and committed into the project by `import`). When the file exists, git-derived intervals whose `from_commit` is the `rg: import` commit are dropped, so the restored history is the archive's; the first interval for a node that begins after the import commit, git-derived or uncommitted, closes the archive's open interval for that node per the rule above; duplicates collapse. Rebuild and incremental extension must yield the same rows.

### `--as-of` (decision 008)
`--as-of <time|commit>` evaluates against the index, never a checkout. A commit maps to its committer time. The boundary is inclusive: a record or interval whose time is ≤ the as-of time is in effect. `now` must not be earlier than `HEAD`'s committer time; a write with an earlier `now` raises `clock_skew`. Two commits with the same `now` are indistinguishable to `--as-of <time>`, so tests advance `--now` between writes. v0.1 reconstructs: node `status` from `status_intervals`; edge existence from `created` and `retired_at`; `approvals` records from `at`; `artifacts` records from `added_at`. `budget`, `usage`, `tags`, `priority` and `lease` are reported at their current values; this is a documented limitation. Under `--as-of`, `lease_expired` uses the as-of time as `now` and is false when the reported `lease` is null, so a historical `running` snapshot whose current lease is null is never `ready`. `export` uses the as-of time only to choose which nodes and edges to archive (decision 015); the archived files are current bytes and `history.jsonl` is complete for those nodes, so every archive satisfies I4 and the interval consistency that `import` checks, and `--as-of` on the imported project reproduces the source's historical reads for the archived members.

### Import clock (decision 012)
Before creating destination files or a git repository, a valid archive is checked against the import call's `now`. The clock floor is the maximum of all restored node `created` and `updated` timestamps, approval `at`, artifact `added_at`, lease `acquired_at` and `expires_at`, edge `created` and `retired_at`, and history interval `from_time` and `to_time`. Ignore null values; an empty archive has no restored clock floor. If `now` is earlier than the floor, raise `clock_skew` and leave the destination absent or empty as it was. Equality is allowed. Invalid timestamp encodings are `bad_archive`, not `clock_skew`.

Import preserves all restored timestamps. In particular, a future lease expiry contributes to the floor; choose an import `now` at or after it rather than rewriting the lease. The import commit is dated `now` as usual, so subsequent writes checked against HEAD cannot close an archived interval before its start.

## Viewer layout
In v0.2 viewer-only state (pan, zoom, selection and collapsed groups) is ephemeral and never writes project files or node fields. `.researchgraph/layout.json` is reserved for a future layout-persistence feature, not created by this viewer.

## Leases and claims
A claim writes `lease` into the node file and commits. A lease whose `expires_at` is at or before `now` is expired: the node is `ready` again (`data-model.md` §6) while its file still says `running`. The file is rewritten only by the next `claim` (which replaces the holder), `renew`, `release`, `complete` or `fail`; until then the recorded holder keeps those rights.

## Artifacts
`complete` records artifacts by path, sha256 and size; it does not move files. Paths must lie under `artifacts/<node id>/`. Projects may use DataLad, DVC or git LFS for large files; the core stores references and stages whatever the path resolves to in the working tree.

## Export archives (decision 012)
The tar stream uses POSIX PAX, not USTAR. Canonical emission is Python `tarfile.open` in uncompressed `w` mode with `format=tarfile.PAX_FORMAT`, `encoding="utf-8"`, `errors="strict"` and an empty global `pax_headers` mapping, followed by zstd level 3 compression. Construct member metadata explicitly rather than inheriting filesystem metadata:
- Include each directory and file in sorted relative POSIX path order; directory names end in `/` and all paths are relative, with no leading `./`.
- Set mtime, uid and gid to 0, uname and gname to empty strings, mode to 0644 for files and 0755 for directories. Directory size is 0; file size is its byte length. Do not copy access times, change times, filesystem permissions, ownership or extended attributes.
- Begin each member with an empty `pax_headers` mapping. Use `tarfile.TarInfo.tobuf(format=tarfile.PAX_FORMAT, encoding="utf-8", errors="strict")` for canonical headers: only necessary automatic extensions are emitted, such as `path` for long or Unicode names and `size` for sizes beyond USTAR's limit. Do not add custom or global PAX records. The standard encoder's local extension-header naming, record ordering and byte-length encoding are part of the canonical form; fixtures must freeze these bytes for long paths, Unicode paths and a header-only large-size case.
- Use the standard tarfile end markers and record padding. Fixed metadata and automatic headers must produce identical bytes for equal member paths and contents, independently of source filesystem metadata.

PAX removes USTAR name and size restrictions; attachment paths are not shortened, renamed or truncated for export.
