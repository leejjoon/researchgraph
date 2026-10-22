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
- Outside-researcher usability session (three timed viewer tasks, at least one consenting researcher outside the team): deferred from Gate 2 by decision 026; must run before Gate 3 and before the v1.0 measurement. Walkthrough U1/U2 were release-blocking and fixed in `bec3700` with real-browser/native regression tests. Retain nonblocking U3–U9/U11 (last-action context, history disclosure reproduction, artifact metadata, retired-card discoverability, wording, incident labels and accessibility investigation); U10 is the accepted field-based expansion-progress limitation. The original walkthrough remains historical, not a post-fix participant retest.
- Second agent-product MCP session (Gate 2 criterion deferred by decision 027): a Codex CLI release that negotiates MCP 2026-07-28 over stdio, another product that does, or a decision narrowing 021 for object-shaped results; then a formal unassisted session under the signed protocol. Required before Gate 3.
