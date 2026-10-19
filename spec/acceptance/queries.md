# queries
1. `ready` never lists a node that is proposed, done, failed or retired, nor a running node with an unexpired lease, nor a node with an unfinished non-retired blocker, nor a node of a non-claimable type. It does list a running node whose lease has expired.
2. `stale` lists claim C1 after any node in its `cites`/`derived-from` closure is superseded, and stops listing it after C1 is superseded with `inherit_edges: false` by C1′ and alice links `cites C1′ → <replacement>` (C1 is then retired and C1′ cites only current evidence). With the default `inherit_edges`, C1′ would inherit `cites → <old>` and be stale itself.
3. `evidence C1` returns the transitive closure described in operations.md and nothing else, excluding C1.
4. `weakest C1` orders results whose inputs are superseded first; among the rest, `runs = 1` before `runs ≥ 3`; among equal runs, `held_out` absent or false before true; ties by id.
5. `alternatives H1` returns exactly the other hypotheses with a non-retired `answers` edge to the same question; if H1 answers two questions, the union of both groups minus H1.
6. `--as-of <commit before approval>` reports H1 as proposed; `--as-of HEAD` reports it active; `--as-of <time between the two commits>` reports it proposed; an edge created after the as-of time is absent from `show`.
7. Every query gives identical output with and without the index file present.
8. `predictions H3` lists every non-retired `tests` edge into H3 with its `expected`; `matrix Q1` has one row per prediction and one column per hypothesis in Q1's group, and flags a row as `non_discriminating` per §6 (all of that prediction's `tests` edges, inside or outside the group, have equal `expected`); a prediction with a single `tests` edge is not flagged.
9. `blocked` lists T2 blocked by T1 with `blocking == [T1]`, and stops listing it when T1 completes or T1's blocking edge is retired.
10. `queue` lists proposed H1 (gated) and not Note N1 (no gate, so created `active`), oldest first, with the gate roles.
11. `since <t>` lists a node approved after `t` even though it was created before `t`, and an edge retired after `t`.
12. `where --type Task --status active --tag x --text "fit"` conjoins the filters; `where --author agent1` matches `author.name`; `where --field kind=expansion` matches `fields.kind`.
13. `digest` after scenario fail 1 reports `failed_24h == 1`; with `--now` 25 hours later it reports 0.
14. `show T1` for a running node with an expired lease reports `lease_expired: true` and `ready: true`.
15. Given active Claim C1 citing independently superseded results R1 and S1 with replacement heads R2 and S2. When querying `stale`. Then C1 occurs once with `responsible` equal to the two head IDs in lexicographic order. With only R1 superseded it is `[R2]`, never a scalar.
16. Given C1 cites R0 and R1, where R1 supersedes R0 and R2 supersedes R1, and a second evidence path also reaches R0. When querying `stale`. Then C1 occurs once with `responsible == [R2]`; repeated paths and intermediate replacements do not duplicate a head. This also holds after index deletion and under `--as-of` using the replacement edges present at that time.
