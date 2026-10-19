# 001: The record is files in git, not a database

Status: accepted · 2026-10-15

## Decision
Nodes and edges are one YAML file each under `graph/` in the project's git repository. The SQLite index is derived and disposable.

## Alternatives considered
- **Beads (Dolt-backed)**: supplies a dependency graph and ready-work detection, but stores data only in Dolt; its JSONL is an export, not the source of truth; its schema is fixed around coding issues. The record would live outside the git history of the notebooks and code it describes.
- **Dolt with our own schema**: cell-level merge and native branching, at the cost of a second version-control system users must learn.
- **Postgres**: easy for the web app, impossible to clone and archive with a paper.
- **DataLad/DVC as the backbone**: solve large files and run provenance, not hypothesis graphs; adopted as the large-file layer instead.

## Consequences
- Conflicts on the same node are ordinary git merges resolved by people.
- Every code path must survive deletion of the index.
- As-of queries are built from git history into an interval table in the index.
