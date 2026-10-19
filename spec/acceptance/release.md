# release
1. Given T1 running by agent1. When agent1 releases. Then status `active`, lease null, T1 is ready again, approvals gains `what: release`.
2. Given T1 running by agent1. When agent2 releases. Then RuleViolation `not_lease_holder`.
3. Given T1 running by agent1. When alice releases. Then success (humans may always release).
4. Given T1 running by agent1 with `usage {tokens: 100}`. When agent1 releases with `usage {tokens: 50}`. Then `usage.tokens == 150`.
5. Given T1 active. When anyone releases. Then RuleViolation `not_running`.
