# 005: Containment, task-to-prediction links, notes, pins, retire, renew, usage, tags, priority

Status: accepted · 2026-10-16

## Decision
Added to the core: edges `part-of`, `addresses`, `about`, `pins`, `duplicate-of`, `relates-to`; node keys `usage`, `tags`, `priority`; operations `retire`, `renew`, `tag`; queries `notes`, `subtree`, `where --tag/--text`; a `Reference` node type and a `cites` schema with a locator in the research profile; viewer layout kept in `.researchgraph/layout.json`.

## Why
A review of the spec against the viewer screens and the astroharness needs found four bugs and several omissions:
- Budgets were "per subtree" with no definition of subtree. `part-of` plus a backward closure over `tests`/`addresses`/`derived-from` defines it.
- Tasks had no link to the prediction they carry out, so the Predictions→Analyses column in the graph view had no edge.
- An active node could not be abandoned without a replacement.
- Leases could not be renewed, so long runs lost their claim mid-run.
- Usage had nowhere to be written, since node `fields` are immutable.
- Pinned evidence and claim locators (cell 23, figure 2) had no representation; edge fields (decision 004) make both cheap.

## Not added
Notifications, due dates, templates, cross-project references, reopening done nodes.
