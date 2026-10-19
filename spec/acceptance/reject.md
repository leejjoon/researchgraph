# reject
1. Given H1 proposed. When alice rejects with reason "duplicate". Then status `retired`, the reason is in approvals with `what: reject`.
2. Given H1 active. When alice rejects. Then RuleViolation `not_proposed`.
3. When agent1 rejects anything. Then RuleViolation `human_only_approval`.
