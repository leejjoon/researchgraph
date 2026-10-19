# claim and renew
1. Given T1 active and ready. When agent1 claims T1 for 3600 s at `now`. Then status `running`, lease actor agent1, `acquired_at == now`, `expires_at - acquired_at == 3600`, approvals gains `what: claim`.
2. Given T1 running by agent1 with an unexpired lease. When agent2 claims. Then RuleViolation `lease_held`.
3. Given T1 running by agent1 with an expired lease. Then `ready` lists T1 while the file still says `running`. When agent2 claims. Then success; lease now agent2; T1 is no longer in `ready`.
4. Given T2 blocked by T1 (not done). When anyone claims T2. Then RuleViolation `not_ready`.
5. Given a node whose subtree budget is `{tokens: 0}`. When anyone claims. Then RuleViolation `not_ready`.
6. Given a read-only project. When agent1 claims. Then RuleViolation `read_only`.
7. Given T1 running by agent1 with 100 s left. When agent1 renews for 3600 s. Then `expires_at == now + 3600`. When agent2 renews. Then RuleViolation `not_lease_holder`.
8. Given T1 running by agent1 with 5000 s left. When agent1 renews for 3600 s. Then `expires_at` is unchanged (renew never shortens).
9. Given T1 running by agent1 with an expired lease. When agent1 renews. Then success (the holder keeps its rights until replaced).
10. Given T1 active (not running). When agent1 renews. Then RuleViolation `not_running`.
11. Given Instruction I1, Failure F1 and Note N1 all active. Then `ready` lists none of them and claiming any raises `not_ready` (not claimable).
12. Given T1 and T2 ready with equal priority and `created`. Then `ready` lists them in id order.
