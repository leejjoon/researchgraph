# release
1. Given T1 running by agent1. When agent1 releases. Then status `active`, lease null, T1 is ready again.
2. Given T1 running by agent1. When agent2 releases. Then RuleViolation `not_lease_holder`.
3. Given T1 running by agent1. When alice releases. Then success (humans may always release).
