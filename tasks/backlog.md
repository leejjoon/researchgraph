# Backlog (v0.3+)
Items moved into `tasks/v0.2.md` on 2026-10-21: MCP server, change events, viewer, relation surfacing; `run`/`complete` gates are superseded there by field-conditional activate gates (decision 018 planned).

- Diff view using renderer diffs; timeline scrubber bound to --as-of
- Renderer plugin API; standalone viewer app
- Profile migrations; sharded node directories if needed
- Atlas exporter; hypotree adapter for expansion
- Signed-commit requirement for approvals (validator)
- Sidecar files for approvals/status (open question 2)
- Reference node enrichment (resolve DOI/bibcode metadata) in a profile, not the core
- Full-text index (FTS5) if `where --text` scans become slow
- Layout persistence UI in the viewer (`.researchgraph/layout.json`)
- `export --bundle`: include a git bundle so imported projects keep original commit identities (decision 008)
- Full historical reconstruction (budget, usage, tags, priority, lease under `--as-of`) from git trees (decision 008 limitation)
- Sidecar event log if `approvals` growth on every claim becomes a problem (decision 010)
- Role assignment file or signed-commit validator instead of self-declared roles (decision 010)
- `unlink`: retire a single edge, so a superseded claim can drop stale evidence without `inherit_edges: false`
- Incremental index update if a full rebuild fails the live-update latency target at scale
- Extend profile-added endpoints beyond `cites` (for example `pins`) when a consumer needs it
- Outside-researcher usability session (three timed viewer tasks, at least one consenting researcher outside the team): deferred from Gate 2 by decision 026; must run before Gate 3 and before the v1.0 measurement. Viewer findings U1–U11 from the 2026-10-22 walkthrough are input to v0.3's viewer items.
