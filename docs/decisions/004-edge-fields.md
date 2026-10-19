# 004: Edges carry immutable, schema-validated fields

Status: accepted · 2026-10-16

## Decision
Edges have an immutable `fields` mapping validated by a per-edge-type JSON Schema declared in the profile (empty schema means no fields allowed). The research profile uses it on `tests` edges to record `expected` (what the hypothesis predicts for that test) and `discriminating`.

## Why
A prediction routinely tests several hypotheses, and what matters scientifically is the expected outcome under each one: the degeneracy matrix. A map on the Prediction node keyed by hypothesis id would break under supersession, because node fields are immutable and ids change. Data about a relation belongs on the relation.

## Alternatives
- Map on the node: breaks on supersede, as above.
- A separate "Expectation" node per (prediction, hypothesis): correct but doubles node count and clutters the graph view.

## Consequences
- Edge format gains one key; supersede copies edge fields to recreated edges.
- New queries `predictions` and `matrix`; the viewer can show the matrix in a prediction's inspector.
- Every edge type now has a schema, even if empty, so stray data cannot creep in.
