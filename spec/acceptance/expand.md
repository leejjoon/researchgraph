# expand
1. Given H1 active. When alice expands [H1]. Then one Task node with `fields.kind = expansion` and `fields.targets = [H1]` exists in `active`, and it is ready. No other nodes are created.
2. When alice expands []. Then RuleViolation `no_targets`.
3. When agent1 expands. Then RuleViolation `human_only_approval`.
