# 007: `link` and `attach` act on existing nodes

Status: accepted · 2026-10-19

## Decision
Add the write `link(type, from, to, fields?)`, allowed to humans and agents, which creates exactly one edge between two existing nodes with the same endpoint, schema, cycle and permission checks as edges created by `propose`. It raises `duplicate_edge` when an identical non-retired edge already exists and `reserved_edge_type` for `supersedes`, `explains` and `instructs`, which only `supersede`, `fail` and `instruct` may create.

Add the write `attach(node, artifacts)`, allowed to humans and agents on any non-retired node, which appends artifact records exactly as `complete` does. It exists because artifact paths must lie under `artifacts/<node id>/` and the id is only known after `propose`, so a Result proposed by an agent had no way to carry its files in a profile where Result is not claimable.

## Why
`propose` creates a node and edges incident to that node. Nothing created an edge between two existing nodes, yet pinning a result, adding a blocker, adding evidence to an existing claim, and four acceptance scenarios all require it.

## Alternatives
- An edge-only form of `propose`: `propose` returns a node id and carries gate semantics that do not apply to edges, and `type` would mean two things.

## Consequences
- Write count becomes eighteen with `attach`, `init` and `import`; `export` moves to the reads (decision 008).
- Lifecycle-bearing edges cannot bypass their operation's permission rules.
