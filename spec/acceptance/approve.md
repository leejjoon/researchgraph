# approve
1. Given H1 proposed. When alice approves H1. Then status `active`, `approvals` has one record `{actor: alice, role: owner, what: approve}`, `updated` changed, and the file is committed.
2. Given H1 proposed. When agent1 approves H1. Then RuleViolation `human_only_approval` (the research gate does not admit agents); status unchanged.
3. Given a profile gate requiring role `owner` for Claim. When bob (reviewer) approves claim C1. Then RuleViolation `wrong_role`.
4. Given H1 active. When alice approves H1 again. Then RuleViolation `not_proposed`.
5. Given claim C1 proposed with no `cites` edge. When alice approves. Then RuleViolation from the profile validator (`claim_needs_evidence`).
6. When alice approves H1 with `what: run`. Then RuleViolation `unknown_what`.
7. Given a profile gate for Claim with `agents_allowed: true` and roles `["human", "agent"]`. When agent1 approves C1 with role `agent`. Then success.
