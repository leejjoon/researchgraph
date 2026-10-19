# reject
1. Given H1 proposed. When alice rejects with reason "duplicate". Then status `retired`, the reason is in approvals with `what: reject`.
2. Given H1 active. When alice rejects. Then RuleViolation `not_proposed`.
3. When agent1 rejects anything. Then RuleViolation `human_only_approval`.
4. Given P1 proposed with `blocks P1 → T2` and H1 proposed with `answers → Q1`. When alice rejects P1 and H1. Then both edges are retired, T2 is ready, and `alternatives` for Q1's other members no longer lists H1.
