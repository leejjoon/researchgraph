# retire
1. Given H1 active with `answers → Q1` and `tests P1 → H1`. When alice retires H1 with reason "abandoned". Then status `retired`, both edges retired, `alternatives` of the other hypotheses no longer lists H1, and `approvals` records `what: retire`.
2. Given T1 running. When alice retires T1. Then RuleViolation `running` (release first).
3. Given H1 retired or done. When alice retires. Then RuleViolation `terminal_status`.
4. When agent1 retires. Then RuleViolation `human_only_approval`.
