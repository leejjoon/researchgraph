# expand
1. Given H1 active. When alice expands [H1] with hint "try two-component fit". Then one Task node with `fields.kind = expansion`, `fields.targets = [H1]`, body "try two-component fit" exists in `active`, and it is ready. No other nodes are created.
2. When alice expands []. Then RuleViolation `no_targets`.
3. When agent1 expands. Then RuleViolation `human_only_approval`.
4. Given a profile whose activate gate covers Task. When alice expands [H1]. Then the Task is `proposed`.
