# Agent committee walkthrough of the viewer usability tasks, 2026-10-22

**This is an agent walkthrough standing in for the outside-researcher session under
decision 026. It is not a usability session with a researcher; `participant_count_achieved`
stays at 0 and the Gate 2 usability cell is recorded as substituted, outside researcher
pending.**

Protocol: `docs/evaluation/usability-session.md` (signed 2026-10-22). Source `5b304af`.
Two participants from different provider families, neither of which planned or built the
viewer, each on its own project seeded by the protocol's preparation block (approved
Hypothesis and Prediction, one failed Task with a registered 23-byte artifact and its
Failure), served by `rg serve --no-open` on loopback, driven through a headless browser,
one task card at a time, no hints, no access to source or documentation. The moderator
(the maintainer) verified native state after each participant finished.

| Participant | Product and model | Browser tool | Mode |
| --- | --- | --- | --- |
| w01 | Claude Code via Paseo, claude-fable-5-1 | gstack `/browse` headless Chromium (the product's own skill) | Paseo auto |
| w02 | Codex CLI via Paseo, gpt-6-astra | operator-supplied generic headless Chromium driver (`browse.py`, Playwright, persistent page) | Paseo full-access, because the Codex workspace sandbox blocks loopback networking from shell commands |

## Task outcomes

Timings are the participants' own `date -u` readings around each card; the 600 s cap applied.

| Participant | Task | clock_start_utc | clock_end_utc | task_elapsed_seconds | Outcome | Assistance | Native verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| w01 | inspect | 2026-10-22T15:23:47Z | 2026-10-22T15:25:08Z | 81 | success-unassisted | none | failed Task `ta-ui67qkad`; Failure `fl-ombea4w7` step/check/measured/error read correctly; artifact downloaded to `/tmp/walk-dl/sample.csv`, 23 bytes, SHA-256 `5a5e1e63…` equal to the record |
| w01 | re-run | 2026-10-22T15:25:13Z | 2026-10-22T15:27:06Z | 113 | success-unassisted (one misstep corrected within the card) | none | `ta-ui67qkad` retired; replacement `ta-iwzi4j4v` with `supersedes` and new `addresses`; Instruction `in-7mhk6uvo` with the exact text via a separate Instruct action after the re-run form dropped it; no lease, approvals or artifacts on the replacement; commits `dca272c`, `7f10d63` |
| w01 | expand | 2026-10-22T15:27:13Z | 2026-10-22T15:28:28Z | 75 | success-unassisted | none | `ta-wmfsbqyu` with `fields.kind=expansion`, `targets=[hy-icmqmint]`, hint in body, no lease or claim; commit `ec9ec53` |
| w02 | inspect | 2026-10-22T15:34:29Z | 2026-10-22T15:35:25Z | 56 | success-unassisted | none | failed Task `ta-4cyydl2n`; Failure `fl-u34r6fbi` fields read correctly; artifact downloaded, 23 bytes, SHA-256 `5a5e1e63…` equal to the record |
| w02 | re-run | 2026-10-22T15:35:29Z | 2026-10-22T15:36:26Z | 57 | success-unassisted | none | `ta-4cyydl2n` retired; replacement `ta-2hdtyhqo` with `supersedes` and `addresses`; Instruction `in-rak7vace` with the exact text created in the same `supersede` commit `d4441a1`; no lease, approvals or artifacts |
| w02 | expand | 2026-10-22T15:36:30Z | 2026-10-22T15:37:08Z | 38 | success-unassisted | none | `ta-loy4cnll`, `kind=expansion`, `targets=[hy-64l7wvuo]`, hint in body, no lease; commit `adff000` |

Both participants completed all three cards, every timing is far under the 600 s target, and
both correctly reported that re-run and expand record requests without executing anything.
These timings say nothing about a human researcher's time; agents read a page in seconds.

## Findings (merged, deduplicated)

| ID | Observed behaviour and impact | Severity | Source | Disposition |
| --- | --- | --- | --- | --- |
| U1 | In the re-run form, typing into Instruction auto-ticks "Set Instruction"; a deliberate tick afterwards unticks it and the instruction is silently dropped. The apply result gives no hint. w01 lost the instruction and recovered with a separate Instruct action. | high | w01 F1, w02 F5 (form complexity) | open; T13/T15 viewer |
| U2 | Action results are raw JSON blocks with no sentence stating what was created or retired and no link to the new node. | high | w01 F2 | open |
| U3 | The last action's result block persists while a different node is selected. | medium | w01 F3 | open |
| U4 | "Full history result" group opened nothing on two attempts. | medium | w01 F4 | open; verify whether `history` renders for a node with a supersedes chain |
| U5 | Artifact download is a bare path link beside a JSON blob; size, hash and adder only readable in JSON. | medium | w01 F5 | open |
| U6 | Retired nodes vanish from the card list with no count; "Show retired" alone did not reveal the retired Task for w02, "Expand history" was also needed. | medium | w01 F6, w02 F3 | open |
| U7 | "Active" on ready Tasks reads like execution; "Re-run" and "Expand" suggest immediate execution. Both participants reached the right conclusion only via the monitoring panel and the form's own "does not execute work" note. | medium | w02 F2, F6 | open; wording |
| U8 | Monitoring labels "Native digest", "Authoritative readback", "Claims — native where membership" are implementation vocabulary. | medium | w02 F4 | open; wording |
| U9 | Three identical "Full node record" / "Full edge record" groups under Incidents; the id and type are not in the group label. | low | w01 F7 | open |
| U10 | The expansion Task is not counted in the Hypothesis progress row because it is linked by a field, not an edge. | low | w01 F8 | accepted limitation (spec: `targets` field); note in the panel |
| U11 | Selected-node explanatory text did not appear in w02's snapshots; the card list missed a late-added card in w01's accessibility listing. Both participants flagged these as possibly their tool. | low, unverified | w01 F9, w02 F1 | check accessible names on inspector sections and late-added cards |

## Tooling incidents (recorded, not scored)

The Codex participant needed five launches. Attempt 1 had no browser tool (valid "blocked").
Attempts 2 to 4 were blocked by defects in the operator-supplied driver: a relative path that
did not resolve from the agent's working directory; session cookies lost between commands,
so the single-use launch token was consumed and later commands saw "Authentication
required"; and a fresh page load per command, which discarded the in-page selection so no
inspector ever appeared. Each attempt used a fresh launch token on the untouched project.
The viewer was not at fault in any of them; the Codex participant's "cannot tell driver from
viewer" findings in those attempts are retained in the custody store but not scored. Only
the fifth attempt, with a persistent-page driver, is scored above.

## Custody

Participant reports as delivered (home directory redacted) are beside this file. Seed
manifests, preparation outputs, server logs, driver, downloads, and the Codex attempt logs
are in the operator's custody store, inventoried as
`usability-walkthrough-2026-10-22-inventory.sha256` (SHA-256 of that file `203a8ee82a22b0f44f3fe9268a1f059b2e7b02e8ad2d0df4f4f90a6e674b87a8`); verify
by that digest in the restricted custody register.
