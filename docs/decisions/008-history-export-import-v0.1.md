# 008: What v0.1 reconstructs for `--as-of`, and how export and import preserve it

Status: accepted · 2026-10-19

## Decision
1. `--as-of T` in v0.1 reconstructs: node `status` from `status_intervals`; edge existence from `created` and `retired_at`; approvals from their `at`; artifacts from their `added_at`. `budget`, `usage`, `tags`, `priority` and `lease` are reported at their current value. This limitation is documented in `store-layout.md`.
2. `export` writes the status intervals into the archive as `.researchgraph/history.jsonl`; `import` commits that file into the new project. The file never appears in a project that was not imported. Index rebuild is the union of git-derived intervals and that file, so a project restored from an archive answers `history` and `--as-of` identically after `rm index.sqlite`.
3. `import` is a v0.1 operation. `export` is a **read**: it writes nothing inside the project.
4. Incremental export (`--since`) includes both endpoints of every included edge, then closes over `supersedes`, `cites` and `derived-from`.
5. Preserving original commit identities through a git bundle is deferred to the backlog (`export --bundle`).

## Why
The spec gave every query `--as-of` but stored only status intervals, so blocked, evidence and subtree had no historical data; export promised that every query survives import but a rebuilt index on a fresh git log cannot reproduce history. Full reconstruction from git trees would need a checkout or a versioned-state cache per commit and cannot meet T12's budget (200-commit scrub of a 5k-node project in under 2 s) without the very interval table it would demote. The limited form keeps files as the source of truth because `history.jsonl` is a file.

Export was classed as a write only because it is not a query. Reviewers are the read-only audience the proposal names, and they are exactly who needs to export.

## Consequences
- `export.md` scenario 1 is reworded to name the files that are byte-identical and the queries that must agree, including `--as-of` for any time recorded in `history.jsonl`.
- `zstandard` is added as a dependency (decision 011).
