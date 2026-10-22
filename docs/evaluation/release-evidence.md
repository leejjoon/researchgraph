Status: release blocked

# Release evidence index and remaining boundaries

This readable index preserves the supplied local proof receipts and unresolved
release conditions. The **runtime ledger is canonical** for gate state, evidence
receipts and owner decisions; this document is not a replacement ledger or an
approval authority. Writing these protocols does not run evaluations or close
gates. No pushes have been performed by this documentation work.

This index was first written as the kit-preparation snapshot after `3a0477d` and refreshed on 2026-10-22 at `5adaca9` and after agent session s01-1. For readers of the repository, git history and CI are canonical; the workflow ledger is the maintainer's local record. Pending verification labels
below describe that snapshot, not a claim that later checks never ran. Subsequent
exact-commit results belong in the canonical ledger with their actual revision;
they do not turn these historical receipts into proof of a different HEAD.

## Local task / proof index

| Item | Recorded local evidence | Scope / remaining boundary |
| --- | --- | --- |
| T16 | Completed at `44baf38` | Retain its task-specific proof; not a final release approval. |
| T17 source boundary | `3a0477d70311081c3e3c95516ac236c32d9c7128` | Source of the recorded clean-clone cohort below, not a full final release HEAD proof. |
| T17 clean-clone receipt | `/tmp/researchgraph-release-3a0477d`, clean before and after; CPython 3.14.4; fresh optional install | Docs + `serve_docs` + notebook + CI cohort: **96 passed**, 42.53s. Legacy JUnit path: `/tmp/researchgraph-clean-3a0477d-docs.xml`. These are existing local receipts, not a newly run check in this documentation change. |
| T17 at `5adaca9` | Exact clean clone at `5adaca9`: full suite **3,785 passed**, 2,530.87 s, no warnings or skips, all 120 strict latency checks and the unchanged historical/rebuild budgets; astroharness `0c0ff07` installed against the current wheel, 15 passed (receipt in the workflow ledger, 2026-10-22) | Candidate readiness is established at `5adaca9`. The run is repeated at the final SHA before any tag; the earlier 96-test cohort at `3a0477d` is retained as historical. |
| Separate-profile astroharness adoption | Commits `aec49a3` / `0c0ff07`; 15 existing/new lifecycle tests against built core `447bb9a` | Authentic separately rooted consumer adoption is already proved. Do not repeat the stale claim that external profile adoption is missing. Preserve this positive evidence independently of the still-open human/product evaluations. |
| G004 concrete repair | Query/evidence repair `0553138`; final-source 131 regression + 4 strict-load + 4 consumer checks done; all 80 samples durable | Concrete repair and retained samples are done. G004 remains review-blocked at the formal final boundary; do not infer final approval from completed checks. |
| G006 formal boundary | pending | Formal final-boundary disposition remains pending, not falsely complete. |
| Final review cohort | pending | Final single heavy cohort and distinct critic review remain pending; previous task-local results do not substitute. |

The local cohort above is precisely a scoped result. It is **not** a claim of a
full suite, hosted GitHub Actions success, external agent sessions, outside-user
research, final reviewer approval or final release HEAD verification. Any future
receipt must state its actual source commit and command/cohort; do not relabel an
older green result as proof for changed source. Temporary `/tmp` paths identify
historical receipts, not a retention guarantee; preserve approved bytes and hashes
in the durable evidence store before relying on their continued availability.

## Protected notebook and local task disposition

| Artifact / boundary | Recorded state | Required treatment |
| --- | --- | --- |
| Committed notebook | SHA-256 `ce3e76651da923cb3ce67e87dba66c9d23a2a7f9a9be69cbfc5b6b40380c7a3c` | Committed proof boundary; do not overwrite it to match unrelated workspace state. |
| Protected dirty notebook | SHA-256 `8e8815e624f6cd523de4422fd22b555b196400286bdd356ccadf0ba06a52cd07` | Pre-existing user state, not release evidence silently substituted for the committed notebook. Neither notebook version was overwritten by this documentation work. |
| G001 / T02 | Closed: the owner committed the accepted `tasks/v0.2.md` amendments and review resolutions in `190edc5` | Resolved; the implementation had been built to the amended text. |

Protected notebook, tasks, review files, lock, checkpoints and incident material
remain outside this documentation change. Their presence is not authorization to
clean, stage or include them. A final-boundary review must explicitly resolve the
owner's inclusion disposition rather than treating a dirty worktree as evidence
that a release commit contains those bytes.

## Gate 2: still pending human/product evidence

| Requirement | Current status | Required actual evidence |
| --- | --- | --- |
| Agent protocol product-owner review | approved 2026-10-22 by the product owner after committee review (see the review table in agent-session.md) | Owner decision for [agent-session.md](agent-session.md), not author self-approval. |
| Usability protocol product-owner review | approved 2026-10-22 by the product owner after committee review (see the review table in usability-session.md) | Owner decision for [usability-session.md](usability-session.md), including consent/capture and reviewed seed. |
| Claude Code | **completed-unassisted** 2026-10-22, attempt s01-1 on 2.1.306 / claude-fable-5-1; checker exit 0; reviewer ten-step audit passed; report [sessions/agent-claude-code-s01-1.md](sessions/agent-claude-code-s01-1.md); recorded_product_count: 1 of 2 | Recorded. Does not close Gate 2 alone; the second product and the outside researcher remain open. |
| Codex CLI | **deferred to Gate 3 (decision 027), failed attempts retained**: 0.155.1 and 0.163.0 negotiate 2025-06-18 over stdio; decision 021 kept ([sessions/preflight-2026-10-22.md](sessions/preflight-2026-10-22.md)); recorded_product_count stays 1 of 2 | A formal unassisted session on a product that negotiates 2026-07-28 over stdio, or after a later decision narrows the refusal; required before Gate 3. |
| Outside-researcher usability | **substituted for v0.2 (decision 026)**: agent committee walkthrough 2026-10-22, two participants, all six task attempts success-unassisted, 11 findings ([sessions/usability-walkthrough-2026-10-22.md](sessions/usability-walkthrough-2026-10-22.md)); pending outside researcher; participant_count_target: 3; participant_count_achieved: 0 | At least one consenting researcher outside the team, carried forward as a debt to be run before Gate 3 and before the v1.0 measurement; the walkthrough is expert review, not outside-user proof. |
| Separate-profile adoption | already proved | Preserve `aec49a3` / `0c0ff07`, built core `447bb9a` and the 15 lifecycle-test receipt; this is not an open missing-adoption cell. |

No participants or agent sessions were run to create this evidence kit. Protocols
are executable operator instructions, not fabricated reports. Deterministic
replay, browser acceptance, SDK checks, help syntax and successful registration
are not a substitute for real product compatibility, unassisted recordings or an
outside researcher. A real preflight refusal is a finding, not permission to
substitute a different client or report success. A native checker pass establishes
only its declared-actor/graph/history contract, not authenticated model identity,
absence of human coaching, consent or protocol-owner review.

## Practical evidence custody and naming

1. Use a private evidence root controlled by the custodian, separate from agent
   workspaces. Name files `release-<source-shortsha>-<gate>-<attempt>-<kind>.<ext>`,
   `agent-<product>-<pseudonymous-id>-<attempt>-<kind>.<ext>` and
   `usability-<pseudonymous-id>-<attempt>-<task-or-session>-<kind>.<ext>`.
   Do not put secrets or real participant names into filenames.
2. Retain actual commands, executable/package versions, source commit, UTC
   start/end, monotonic durations, exit codes and stdout/stderr. Preserve failed
   attempts and review-blocked dispositions as well as passing checks. For each
   file inventory its relative path, byte size and SHA-256 over actual bytes;
   `sha256sum /absolute/path/to/actual-file` is one suitable command. Include all
   logs/results, JUnit, native snapshots, manifests, prompt/configuration,
   recordings/transcripts, checker outputs and redacted derivatives. Hashes do
   not authenticate a participant or grant approval.
3. Preserve trusted seed manifests/checkers outside agent-write authority.
   Retain native graph YAML, artifact bytes and Git history with the exact seed
   and end HEADs. Link reports to those receipts, not only to screenshots or
   mutable SQLite caches. Record capture gaps and unavailable legacy files as
   limitations, never generate replacement fake logs/transcripts.
4. Obtain consent before collection and an explicit access list, withdrawal
   process, custodian and retention/deletion date. Keep consent identities apart
   from pseudonymous reports. Restrict originals; redact tokens, cookies, launch
   fragments, configuration secrets, identifiers and unrelated screen content.
   Record original/derivative hashes, redaction reasons and privacy-review
   decisions. Delete restricted originals and backups at the agreed deadline;
   keep only consent-permitted deletion receipts/derivatives.
5. After privacy and outcome review, commit consent-safe reports and approved
   derivatives (agent transcripts or exports, checker outputs, task tables,
   findings) under `docs/evaluation/sessions/`, keeping restricted originals in
   the custody store with inventory references, and update the counts here. Empty tables in the protocols are schemas, not sessions. No blank
   recording or invented measured value may stand in for missing evidence.
   Record independent reviewer decisions with evidence references in the runtime
   ledger. Do not edit this index to manufacture owner approval or completion.

## Release remains blocked until actual disposition

At kit preparation, unresolved boundaries are protocol-owner reviews; real Claude Code and Codex
CLI recordings/checker outcomes; outside-researcher evaluation; G001 protected
T02 owner inclusion disposition; G004 review-blocked/G006 pending formal final
boundary; T17 full final clean clone; and the final single heavy cohort with a
distinct critic. Authentic astroharness adoption and the concrete G004 repair
remain completed facts, not reopened missing work. No final release, full-suite,
hosted Actions or external/final review claim is made here.
