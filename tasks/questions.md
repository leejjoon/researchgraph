# Questions for the maintainers
(Agents: add a dated entry here when the spec is silent on something that affects file formats or the public API. Do not guess.)

## 2026-10-19 — provisional answers from the spec review (decisions 006–011)
The committee review of the spec left three design splits, which the maintainer resolved (limited `--as-of`, `claimable` flag, forbid supersede on running). The following further choices were made while applying the review and are recorded in decision records; confirm or overturn with a new record.

1. **Export is a read**, so read-only reviewers can export (decision 008). Overturn → move the row back to Writes.
2. **Roles are self-declared per call** (`--role`), not stored in a file (decision 010). An `actors.yaml` or signed-commit check can be a validator later.
3. **Timestamps are whole seconds**; `ready` ties break on id (decision 011).
4. **Top-level keys are always written with explicit `null`; keys inside `fields` and sub-mappings are omitted when absent** (decision 011).
5. **Profile-added edge types default to `inherit: outgoing`** (decision 006).
6. **`approvals` is the per-node event log** and gains `claim`, `renew`, `release`, `complete`, `fail`, `superseded`, `budget` records (decision 010). This grows node files on every claim; a sidecar is the backlog alternative.
7. **Agent commit author** is `NAME <NAME@agents.researchgraph>` (decision 010).
8. **Artifacts must live under `artifacts/<node id>/`**; `propose` does not take artifacts (the id is not known yet); a new `attach` write appends them to an existing node (decision 007).
