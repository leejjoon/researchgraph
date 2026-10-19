# claim
1. Given T1 active and ready. When agent1 claims T1 for 3600 s. Then status `running`, lease actor agent1, `expires_at - acquired_at == 3600`.
2. Given T1 running by agent1. When agent2 claims. Then RuleViolation `lease_held`.
3. Given T1 running by agent1 with an expired lease. When agent2 claims. Then success; lease now agent2.
4. Given T2 blocked by T1 (not done). When anyone claims T2. Then RuleViolation `not_ready`.
5. Given a node whose subtree budget is `{tokens: 0}`. When anyone claims. Then RuleViolation `not_ready`.
6. Given a read-only project. When agent1 claims. Then RuleViolation `read_only`.
7. Given T1 running by agent1 with 100 s left. When agent1 renews for 3600 s. Then `expires_at` is ~3600 s from now. When agent2 renews. Then RuleViolation `not_lease_holder`.
