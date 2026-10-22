# 026: For v0.2, an agent committee walkthrough stands in for the outside-researcher usability session

Status: accepted · 2026-10-23 · amends the Gate 2 usability cell for v0.2 only

## Decision
1. Gate 2's "researcher usability session run" is **deferred** for v0.2. In its place a two-member agent committee performs a walkthrough of the three usability task cards under `docs/evaluation/usability-session.md`: fresh agents from two provider families, who did not plan or build the viewer, each on its own seeded project per the protocol's preparation block, driving the real viewer through a browser, one card at a time, with no hints and no access to the researchgraph source or documentation during the tasks. Timings, outcomes, assistance and findings are recorded in the protocol's schema; native state is verified afterwards exactly as for a human participant.
2. The Gate 2 usability cell is recorded as **substituted, outside researcher pending**. It is never marked complete, and `participant_count_achieved` stays at zero. The outside-researcher session is carried forward as an explicit debt to be run before Gate 3 (end of v0.3) and in any case before the v1.0 usability measurement.
3. A v0.2 tag may be made with this substitution recorded in the evidence index and the Gate 2 closure note.

## Why
The maintainer is one person and no outside researcher has been recruited. A committee walkthrough is an expert review: it finds coherence and navigation problems and establishes that the tasks can be completed from the cards alone. It is not user testing. An agent reads the interface in seconds and does not hesitate over a label, so it cannot measure the proposal's ten-minute target for a researcher outside the team. Recording the substitution plainly keeps the ledger rule "never mark incomplete external cells complete" true while letting v0.2 close on evidence that is either recorded or explicitly substituted.

## Alternatives
- Keep the gate as written and hold v0.2 open until a researcher is found: no technical cost, but v0.3 work on the viewer would start on a viewer nobody outside the team has used.
- Count a team demonstration as the session: forbidden by the signed protocol.

## Consequences
- `tasks/v0.2.md` Gate 2 paragraph and `docs/evaluation/release-evidence.md` gain the substitution wording.
- The walkthrough report lands under `docs/evaluation/sessions/` labelled as an agent walkthrough.
- `tasks/backlog.md` gains the deferred researcher session as a v0.3 gate item.
