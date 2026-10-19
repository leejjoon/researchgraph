# complete
1. Given T1 running by agent1 and a file `artifacts/<T1>/out.csv`. When agent1 completes with that artifact and summary "done". Then status `done`, lease null, `artifacts[0].sha256` equals the file's hash and `bytes` its size, approvals gains `{what: complete, note: "done"}`, and the commit includes the node file and `artifacts/<T1>/out.csv`.
2. Given T1 running by agent1. When agent1 completes citing a path that does not exist. Then RuleViolation `artifact_missing`; status unchanged.
3. Given T1 running by agent1. When agent2 completes. Then RuleViolation `not_lease_holder`.
4. Given T2 blocked by T1. When T1 completes. Then `ready` now lists T2.
5. Given T1 running by agent1. When agent1 completes with `usage {tokens: 1200}`. Then `show T1` reports usage and `subtree H1` (if T1 addresses a prediction under H1) sums it.
6. Given T1 running by agent1. When agent1 completes citing `artifacts/<T2>/x.csv` or `data/x.csv`. Then RuleViolation `artifact_path`.
7. Given T1 done. When agent1 completes again. Then RuleViolation `not_running`.
8. Given T1 running by agent1 with an expired lease and nobody has reclaimed it. When agent1 completes. Then success.
