# 003: Append-only nodes and edges; blocked and ready are derived

Status: accepted · 2026-10-15 · amended by 005, 006, 010

## Decision
A node's meaning (type, title, body, fields, author, created) never changes after creation. Only status, approvals, artifacts, budget and lease are mutable. Edges are never edited: superseding a node retires its edges and creates new ones from the replacement. `blocked` and `ready` are not stored; they are computed from `blocks` edges and approval state.

## Why
Append-only keeps the graph acyclic by construction and keeps every abandoned idea queryable. Deriving readiness avoids a stored status that can drift from the edges that define it.

## Consequences
- `supersede` is the only way to change what a node says.
- Validators refuse any diff that changes an immutable field of an existing node file.
