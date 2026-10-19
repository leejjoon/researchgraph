# Researchgraph Spec Review

## Provenance

| | |
| --- | --- |
| Subject | The normative specification of researchgraph: `spec/data-model.md`, `spec/operations.md`, `spec/profiles.md`, `spec/store-layout.md`, `spec/acceptance/*.md`, read together with `AGENTS.md`, `tasks/v0.1.md`, `docs/proposal.md` and `docs/decisions/001`–`005` |
| Spec version reviewed | commit `2205caa` ("initial commit", committed 2026-10-19; spec text dated 2026-10-15 and 2026-10-16), the state before any of the changes this review caused |
| Review date | 2026-10-19 |
| Requested by | Jae-Joon Lee (maintainer) |
| Method | `/paseo-committee`: two agents with no shared context reviewed the spec independently, then converged over three rounds (independent reports; rulings on ten contested points against the spec text; final positions on four residuals) |
| Committee members | Claude Fable 5.1 (plan mode, xhigh reasoning) and OpenAI Codex GPT-6-Astra (xhigh reasoning), both run through Paseo 0.12.0 on host z2mini |
| Synthesis | Claude Fable 5.1 (Claude Code session), which wrote the prompts, relayed the reports between members and drafted this document |
| Maintainer decisions | The three splits in *Maintainer decisions* were resolved by the maintainer on 2026-10-19 by accepting the recommendations |
| Applied in | The same commit that adds this file: decision records `docs/decisions/006`–`011`, the rewritten `spec/`, the revised acceptance suite and task list. Further provisional choices made while applying are listed in `tasks/questions.md` |
| Also published | https://claude.ai/code/artifact/34dea7a9-31cb-40c5-a308-9e01af7a4f65 (private Claude doc, same content) |
| Full transcript | Kept outside the repository in the session scratchpad; the two agents remain in the Paseo workspace under "[Committee] Spec review" |

## Verdict

The spec at `2205caa` was not ready to implement test-first without guessing. The architecture and the five decision records are coherent, and nobody proposed a redesign. But an implementer would have had to invent on-disk details in about ten places, four acceptance scenarios were unreachable because the operation they need did not exist, and several contradictions would have yielded different behaviour depending on which file was trusted. Each fix is a paragraph or a table column.

## Findings

Numbered continuously. Section references are to the reviewed version. "Resolution" names where the fix landed.

### File format and public contract

| # | Where | Problem | Resolution |
| --- | --- | --- | --- |
| 1 | store-layout.md, File format | Canonical YAML underspecified: null versus omitted keys, quoting of strings YAML 1.1 coerces, block style for body, line width, timestamp precision, nested key order. "YAML 1.2" promised with PyYAML, a 1.1 library. | Decision 011; *Canonical form* in store-layout.md; fixtures required by T02 |
| 2 | operations.md, Writes; data-model.md §2 | `complete.summary`, `fail.summary`, `release.note`, `expand.hint` and the `approve.what` vocabulary had no storage location. Profile defaults unresolved against "absent keys are null". | Decision 010; data-model.md §2.2 (approvals event log) and §2.3 (generated nodes) |
| 3 | operations.md, propose | No operation created an edge between two existing nodes, yet propose 6, 7, 9 and notes-and-pins 2, 4 needed one. | Decision 007; `link` write; `link.md` |
| 4 | operations.md, actor; data-model.md §2 | Stored shape of `lease.actor` and `approvals[].actor`, the lease-holder equality key, the git author for agents, and where a human's role is recorded were undefined. | Decision 010; data-model.md §2.1; roles self-declared per call |
| 5 | profiles.md, bundled profiles | `tasks` profile lacked Failure and Instruction; no Task schema had `fields.command`; `{}` as an edge schema accepts anything in JSON Schema. | profiles.md: closed schemas, `command` on Task, both profiles complete; data-model.md §8 reserved vocabulary |
| 6 | operations.md | `init` and `import` unspecified; tar.zst needed an undeclared dependency. | Decision 008 and 011; `init` and `import` rows; `init.md`, `import.md` |
| 7 | data-model.md §2, id | Base32 alphabet and collision policy unstated. | data-model.md §2: regex and regenerate-on-collision |

### Correctness

| # | Where | Problem | Resolution |
| --- | --- | --- | --- |
| 8 | operations.md, supersede; data-model.md §6 stale | Retargeting incoming `cites` edges to the replacement meant a Claim was never stale, so queries 2 could not pass. Repeated supersession retired the old `supersedes` edge and broke the history chain. | Decision 006; `inherit` column; transferable-edge rule; `supersede.md` 9–13 |
| 9 | store-layout.md, Leases; data-model.md §5–6; queries 1; claim 3 | Four texts disagreed on whether a running node with an expired lease is ready; holder rights after expiry undefined. | Decision 010; `lease_expired(n)`; running→running claim; renew never shortens |
| 10 | profiles.md, Gate; operations.md, approve | `run` and `complete` gates could never be satisfied; `requires_gate` and `gates` could disagree. | Decision 009; activate-only; `requires_gate` removed |
| 11 | operations.md, reject | `reject` left edges live: a rejected blocker blocked forever. | Decision 010; reject retires edges; `reject.md` 4 |
| 12 | data-model.md §6 over_budget; budget 2, 3 | "Exceeds" made a zero budget not stop work; budget 2 read a dead `tokens_used` field; `release` lacked `usage`; delta versus cumulative undefined. | Decision 010; inclusive comparison; additive usage; `release(usage?)` |
| 13 | export 2 | Incremental closure over three edge types omitted endpoints of other included edges, violating I5 on import. | Decision 008; endpoint closure in the export row |
| 14 | store-layout.md, Writes | No intra-checkout write lock. | store-layout.md Writes: `flock` on `.researchgraph/lock` |
| 15 | store-layout.md, Index | Staleness keyed on the committed tree hash missed `--no-commit` writes and hand edits. | store-layout.md Index: content hash, re-checked per operation |
| 16 | operations.md, Reads | `weakest` sort direction contradicted queries 4; `ready` had no null-priority or tie-break rule; `non_discriminating` defined two ways; `alternatives` assumed one exclusion group; `since` had no clock; subtree scenario 3 excluded the root. | operations.md Reads; data-model.md §6; `queries.md`, `notes-and-pins.md` 3 |

### Clarity and coverage

| # | Problem | Resolution |
| --- | --- | --- |
| 17 | No scenarios for blocked, queue, since, digest, most where filters, `--as-of` with a time, renew or complete or fail on a non-running node, supersede on a running node, cycles through derived-from or cites, not_found, profile_too_new, read_only beyond claim. Propose 3 lacked `expected`; tag 3 was garbled. | `general.md`; `queries.md` 9–14; scenarios added across the suite |
| 18 | T05 required `propose` before T08 existed; T07 needed operations before T08; T11 omitted query scenario 8; T18 named no linter; `pyproject.toml` carried two dependencies without records; the validator `Write` type was undefined. | `tasks/v0.1.md` reordered (queries now T08); decision 011; `Write` dataclass in profiles.md |
| 19 | README said twelve operations; decision 003's mutable list predated 005; the proposal's executive summary put MCP and viewer in v0.1. | README and 003 amended; the proposal is left as historical background |

## Agreed spec changes

Both members signed these after round three.

1. **Edge inheritance on supersede** is declared per edge type (`both`, `outgoing`, `none`). Structural edges transfer; evidence and provenance edges stay attached to the node that was evaluated; `supersedes` edges are never retired. Re-assertion goes through `link`.
2. **Lease expiry** is a derived predicate. An expired running node is ready while its file still says running; the recorded holder keeps its rights until another actor claims; renew never shortens.
3. **Gates** are activate-only in v0.1. `requires_gate` is gone; approval requirement follows from the gates.
4. **`link`** creates an edge between existing nodes; `supersedes`, `explains` and `instructs` are reserved for their operations.
5. **Evidence preserved, renew never shortens**: settled as part of 1 and 2.

## Maintainer decisions

| Question | Claude | Codex | Decision |
| --- | --- | --- | --- |
| What must `--as-of` and export reconstruct in v0.1? | Limited: status intervals plus edge, approval and artifact timestamps; a `history.jsonl` in the archive; mutable metadata at current value as a documented limitation | Full: everything from git trees; export includes a git bundle | Limited (decision 008). The bundle is in the backlog. |
| Are Notes and Failures ready and claimable? | `NodeType.claimable`, true only for Task | Type-agnostic `ready` with `--type`; runner filters | The flag (decision 009) |
| Supersede a running node? | Forbid with rule `running` | Allow and clear the lease atomically | Forbid (decision 010) |

Both members also judged export's classification as a write settled by the text but questionable. It is now a read (decision 008, listed for confirmation in `tasks/questions.md`).

## Open questions and their status

| # | Question | Status |
| --- | --- | --- |
| 1 | Null or empty optional keys written, or omitted? | Decided: top-level keys always written; sub-mapping keys omitted (011) |
| 2 | Timestamp precision? | Whole seconds (011) |
| 3 | PyYAML with quoting rules, or ruamel.yaml? | PyYAML (011) |
| 4 | Where is the human-to-role mapping stored? | Self-declared per call (010); file or signed-commit validator in backlog |
| 5 | Stored actor shape and identity key? | Mapping; `(kind, name)` (010) |
| 6 | Where do summaries and notes go; `approve.what` vocabulary? | Approvals event log (010) |
| 7 | Usage increment or total; retired descendants? | Increment; retained (010) |
| 8 | Artifact staging and path rule? | Staged; under `artifacts/<id>/` (operations.md) |
| 9 | Artifacts on a proposed Result? | `attach` write (007) |
| 10 | Is import a v0.1 operation? | Yes (008) |
| 11 | Is export a read or a write? | Read (008), listed for confirmation |
| 12 | Lock file or single-writer contract? | Lock file (store-layout.md) |
| 13 | Which profile does the runner target? | `tasks`, with `command` on Task |
| 14 | Task list or proposal roadmap? | Task list; proposal left as background |
| 15 | Exact `weakest` sort tuple? | `(has no superseded input, runs or 0, held_out or false, id)` |

## Checked and found sound

- Files as authority and SQLite as a disposable cache are stated consistently.
- The lifecycle table agrees with the operations table and every acceptance file apart from items 9 and 16.
- I3 plus `terminal_status` prevents forked supersession chains.
- Budget propagation over subtree is consistent across all files that mention it.
- Default agent permissions match the proposal's "What agents may do".
- Decision 004 on edge fields is reflected consistently.
- The index-deletion rule appears consistently in store layout, acceptance README, conftest and T18.
- No discipline-specific knowledge in `spec/`. The scientific-method vocabulary is cross-disciplinary and already part of the core edge table, so declaring it reserved is a gap, not a scope violation. (`bibcode` was moved out of the generic research profile when applying the review.)
- No scheduling loop in the core.

## Post-application check

After the changes were applied, an independent agent traced the revised supersede, lease, gate, export and task-ordering rules by hand and found 25 further small conflicts, all fixed in the same commit. The most significant: the first draft of the supersede rule would have retired preserved evidence edges and broken `stale`.
