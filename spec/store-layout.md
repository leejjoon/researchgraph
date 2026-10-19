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

### status_intervals
`(node_id, status, from_commit, from_time, to_commit, to_time)`, `to_*` null for the newest interval of each node. Every new interval for a node, whether git-derived or the uncommitted open interval, closes that node's previous open interval at its own `(from_commit, from_time)`, so a node never has two open intervals. Built by walking `git log --reverse -- graph/nodes` once on first open, then extended per new commit; `from_time` of a git-derived interval is the commit's committer time (which Writes step 6 sets to `now`). The same walk records, for every `approvals[]` and `artifacts[]` entry, the commit that introduced it (null while uncommitted), which `history` reports. Uncommitted state is an open interval with `from_commit` null and `from_time` = the node's `updated`. The rows are the union of git-derived intervals and `.researchgraph/history.jsonl` when that file exists (one JSON object per line with the six keys in that order, no spaces after separators, lines sorted by `(node_id, from_time, status)`; generated by `export` into the archive only and committed into the project by `import`). When the file exists, git-derived intervals whose `from_commit` is the `rg: import` commit are dropped, so the restored history is the archive's; the first interval for a node that begins after the import commit, git-derived or uncommitted, closes the archive's open interval for that node per the rule above; duplicates collapse. Rebuild and incremental extension must yield the same rows.

### `--as-of` (decision 008)
`--as-of <time|commit>` evaluates against the index, never a checkout. A commit maps to its committer time. The boundary is inclusive: a record or interval whose time is ≤ the as-of time is in effect. `now` must not be earlier than `HEAD`'s committer time; a write with an earlier `now` raises `clock_skew`. v0.1 reconstructs: node `status` from `status_intervals`; edge existence from `created` and `retired_at`; `approvals` records from `at`; `artifacts` records from `added_at`. `budget`, `usage`, `tags`, `priority` and `lease` are reported at their current values; this is a documented limitation. Under `--as-of`, `lease_expired` uses the as-of time as `now` and is false when the reported `lease` is null, so a historical `running` snapshot is never `ready`.

## Viewer layout
Viewer-only state (card positions, collapsed groups) lives in `.researchgraph/layout.json`, never on nodes. Projects may commit or ignore it.

## Leases and claims
A claim writes `lease` into the node file and commits. A lease whose `expires_at` is at or before `now` is expired: the node is `ready` again (`data-model.md` §6) while its file still says `running`. The file is rewritten only by the next `claim` (which replaces the holder), `renew`, `release`, `complete` or `fail`; until then the recorded holder keeps those rights.

## Artifacts
`complete` records artifacts by path, sha256 and size; it does not move files. Paths must lie under `artifacts/<node id>/`. Projects may use DataLad, DVC or git LFS for large files; the core stores references and stages whatever the path resolves to in the working tree.
