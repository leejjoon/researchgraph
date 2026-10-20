# 015: `export --as-of` selects members; imported provenance is the import commit

Status: accepted · 2026-10-20 · resolves the two T14 questions of 2026-10-20 in `tasks/questions.md`

## Decision
1. Under `--as-of`, `export` applies the as-of time to **selection only**: the included nodes and edges are those in effect at that time, and with `since` the changed set is what `since` reports under the same `--as-of` (changes after `since` up to and including the as-of time). Every archived `graph/` file and artifact is the working-tree bytes of the selected member, and `history.jsonl` carries the selected nodes' complete status intervals, including those after the as-of time. An archive never contains a historical projection; `--as-of` on the imported project reproduces the historical reads.
2. After `import`, `history` attributes every record restored from a graph file (approval records, artifact records and `supersedes` edges) to the `rg: import` commit. Only status intervals keep the archive's commit ids, because only they are carried in `history.jsonl`. The archive format is unchanged.

## Why
`--as-of` evaluates against the index, never a checkout, and v0.1 reconstructs only status, edge existence, approvals and artifacts (decision 008). A historical node projection keeps the current lease and can violate I4 (`lease` non-null iff `status == running`), and `import` requires each node's open interval to match its stored status, so an archive is self-consistent only as current bytes with complete intervals or as historical bytes with truncated intervals. The second needs lease reconstruction that 008 excluded, or invented leases that decision 012's "import preserves all restored timestamps" forbids. Current bytes with complete intervals also keep every historical read reproducible after import, which is what gives export scenario 1 its parity. Exempting `export` from `--as-of` would instead make it the one read the v0.2 wrappers special-case.

For provenance, the archive carries files, not commits; 008 deferred commit identity to a backlog `export --bundle`. An edge's introducing commit is stored in no file, is already "null while uncommitted", and cannot be inferred from intervals: a `--no-commit` supersede followed by a `--commit` tag puts the replacement node in the tag commit while the edge stays uncommitted. Carrying edge commits would be a file-format change and a partial bundle for a display field.

## Alternatives
- Exempt `export` from `--as-of`: contradicts "Every read accepts `--as-of`" and loses the `--since s --as-of t` window that existing `since` semantics give for free.
- Historical payloads: outside 008's reconstruction scope; a new archive kind for `import` to validate; `--as-of` after import would disagree with the source for times after T.
- Current bytes with intervals truncated at T: fails import's open-interval versus stored-status check.
- Archive edge introduction commits: file-format change; needs union-and-drop rules mirroring intervals; a partial `--bundle`.
- Infer the edge commit from the replacement's first interval: wrong in the counterexample above, and reports a commit where the source reports null.
- Report `commit: null` for restored `supersedes` records, or drop the field: inconsistent with approval and artifact records, or a change to the frozen `--json` shape.

## Consequences
- `operations.md` (`export` row, `--as-of` sentence, `history` row) and `store-layout.md` (`--as-of`, Index) gain the sentences for this decision; `export.md` 1 widens its exception; `export.md` gains scenarios 7–10 and scenario 4 is reworded.
- No archive format change; archive validation and `import` are unchanged. T14's `export` reads raw bytes and keeps complete intervals under `--as-of`; its tests change accordingly, and the history test normaliser treats `supersedes` records like approval and artifact records.
