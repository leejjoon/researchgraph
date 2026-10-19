# Store layout and index (normative)

## Files
```
<project>/
  .researchgraph/
    profile.yaml        # {name: research, version: "1.0"}
    index.sqlite        # gitignored, disposable
  graph/
    nodes/<id>.yaml     # one node
    edges/<id>.yaml     # one edge
  artifacts/<node id>/  # files attached to that node (optional data-versioning layer may manage large ones)
  .gitignore            # includes .researchgraph/index.sqlite
```
`rg init` creates this layout and the first commit.

## File format
- YAML 1.2, UTF-8, 2-space indent, keys in the order listed in `data-model.md`, no anchors, no flow style for mappings. The serialiser is canonical so a no-op rewrite produces no diff.
- Timestamps RFC 3339 UTC with `Z`.
- One file per node or edge, named by id. No subdirectories in v0.1 (sharding is a reserved future change: `graph/nodes/<first two chars>/<id>.yaml`).

## Writes
1. Validate the proposed change against the profile and invariants, using the current index.
2. Write the node/edge file(s) atomically (write temp, rename).
3. Update the index.
4. If `--commit` (CLI default: on) stage and commit the touched files with message `rg: <operation> <id>` and the actor as author.

A write that fails validation touches nothing and returns `RuleViolation{rule, message, ids}`.

## Index
SQLite at `.researchgraph/index.sqlite`, schema version in a `meta` table. Tables: `nodes`, `edges`, `approvals`, `artifacts`, `status_intervals`, `meta`. A `ready` view implements `data-model.md §6`.

- On open: compare `meta.tree_hash` to a hash over `graph/**` (sorted paths + sizes + mtimes, or the git tree hash of `graph/` when available). Mismatch → rebuild from files.
- Rebuild must complete in < 5 s for 20,000 nodes on a laptop (measured in CI with a synthetic project).
- **Deleting the index must never change the result of any query.** CI runs the acceptance suite twice: once with the index kept, once with it deleted before every command.

### status_intervals
`(node_id, status, from_commit, from_time, to_commit NULL, to_time NULL)`. Built by walking `git log --reverse -- graph/nodes` once on first open, then extended per new commit. `--as-of <time|commit>` evaluates queries against the intervals, not against a checkout.

## Viewer layout
Viewer-only state (card positions, collapsed groups) lives in `.researchgraph/layout.json`, never on nodes. Projects may commit or ignore it.

## Leases and claims
A claim writes `lease` into the node file and commits. A lease whose `expires_at` has passed is treated as released by the `ready` query (the node is ready again) but the file is only rewritten by the next `release`, `claim` or `complete`/`fail`. Two claims on one node race through git: the second to push loses the merge and must re-read.

## Artifacts
`complete` records artifacts by path and sha256; it does not move files. Projects may use DataLad, DVC or git LFS for large files; the core only stores references.
