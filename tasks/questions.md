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

## 2026-10-20 — T14 historical export payloads (resolved by decision 015)

`operations.md` gives every read `--as-of`, but historical projections retain the current lease (`store-layout.md`, decision 008). A historical `running` Task can therefore have a null current lease after completion; conversely an historical `active` Task can have a current running lease. Both are valid read projections but invalid stored nodes under I4. Full historical lease reconstruction is outside the approved scope. The export contract also preserves raw graph bytes and status history. Independent contract review confirmed that it does not specify where historical selection stops and archive restoration payloads begin.

Does `export --as-of T` select node/edge membership, change detection and closure historically, while preserving selected current YAML and complete status intervals? This smallest scope-preserving option includes approvals/artifact records after T, and with `include_artifacts` current artifact files under selected nodes; import still checks the maximum restored timestamp including lease expiry. Imported historical reads then apply the usual filtering. Alternatively, should export be exempt from `--as-of`, or should payloads themselves be historical with an explicitly revised lease-reconstruction contract?

No historical-export payload interpretation has been implemented or silently assumed. Default export/import tests can proceed; choosing a payload policy requires a decision record and matching specification clarification before implementation.

**Resolution (2026-10-20, committee, decision 015):** the first option. `--as-of` governs selection only; the archive carries the selected members' current bytes and complete status intervals; an archive never contains a projection. Spec: `operations.md` `export` row and `--as-of` sentence, `store-layout.md` `--as-of`; acceptance `export.md` 4, 7–9.

## 2026-10-20 — T14 supersedes introduction provenance (resolved by decision 015)

Export acceptance 1 permits imported `history` provenance to change only for approval and artifact records. Supersedes history also reports its edge's introducing commit, but canonical archives store only raw graph files and six-field node status intervals; neither encodes that edge commit.

The replacement's first status commit cannot safely supply it: create a supersession with `commit=False`, then tag its replacement with `commit=True`. Scoped commits introduce the replacement node in the tag commit while its supersedes edge remains untracked with null introduction provenance. The same archived node intervals and raw edge data cannot distinguish all such histories. Independent review confirmed this normal-operation counterexample; no provenance inference has been implemented.

Should import's existing history-provenance exception also cover supersedes records, or must the archive representation be explicitly extended to preserve supersedes introduction commits? The former is the smallest scope-preserving change; the latter requires an authorized file-format extension. No acceptance exception or archive field has been silently added.

**Resolution (2026-10-20, committee, decision 015):** the former. Everything restored from a graph file (approval, artifact and `supersedes` records) is attributed to the `rg: import` commit; only status intervals keep the archive's commit ids. Archive format unchanged. Spec: `store-layout.md` Index, `operations.md` `history` row; acceptance `export.md` 1 and 10.
