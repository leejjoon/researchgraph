# complete
1. Given T1 running by agent1 and a file `artifacts/<T1>/out.csv`. When agent1 completes with that artifact. Then status `done`, lease null, `artifacts[0].sha256` equals the file's hash, commit includes the node file.
2. Given T1 running by agent1. When agent1 completes citing a path that does not exist. Then RuleViolation `artifact_missing`; status unchanged.
3. Given T1 running by agent1. When agent2 completes. Then RuleViolation `not_lease_holder`.
4. Given T2 blocked by T1. When T1 completes. Then `ready` now lists T2.
5. Given T1 running by agent1. When agent1 completes with `usage {tokens: 1200}`. Then `show T1` reports usage and `subtree H1` (if T1 addresses a prediction under H1) sums it.
