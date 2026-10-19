# queries
1. `ready` never lists a node that is proposed, running, done, failed or retired, or that has an unfinished non-retired blocker.
2. `stale` lists claim C1 after any node in its `cites`/`derived-from` closure is superseded, and stops listing it after C1 is superseded by a claim citing the replacement.
3. `evidence C1` returns the transitive closure described in operations.md and nothing else.
4. `weakest C1` orders results with `runs = 1` before `runs ≥ 3`, and superseded-input results first.
5. `alternatives H1` returns exactly the other hypotheses with a non-retired `answers` edge to the same question.
6. `--as-of <commit before approval>` reports H1 as proposed; `--as-of HEAD` reports it active.
7. Every query gives identical output with and without the index file present.
8. `predictions H3` lists every non-retired `tests` edge into H3 with its `expected`; `matrix Q1` has one row per prediction and one column per hypothesis in Q1's group, and flags a row whose cells are all equal as `non_discriminating`.
