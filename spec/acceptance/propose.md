# propose
1. Given the research profile. When alice proposes a Hypothesis "H1". Then a file `graph/nodes/hy-*.yaml` exists with status `proposed`, author alice, `updated == created`, and the commit message is `rg: propose <id>`.
2. Given the same. When agent1 proposes a Task "T1" with no edges. Then status is `active` (Task has no gate) and `ready` lists it.
3. Given H1 proposed. When agent1 proposes a Prediction "P1" with edge `tests → H1 {expected: "weak absorbed"}`. Then the edge file exists with `fields.discriminating == true` (schema default materialised) and `show P1` lists it.
4. When anyone proposes a node of unknown type "Widget". Then RuleViolation `unknown_type`, no files written.
5. When alice proposes a Hypothesis whose `fields` violate the schema, including an undeclared key. Then RuleViolation `schema`.
6. Given tasks A and B with `blocks A → B`. When agent1 proposes a Task C with edges `blocks → A` and `blocks B → C` (from B). Then RuleViolation `cycle` and no files written.
7. When agent1 proposes a Task with edge `answers → Q1`. Then RuleViolation `edge_endpoints`.
8. When agent1 proposes a Prediction with edge `tests → H1` and no `expected` field. Then RuleViolation `schema` (research profile requires it).
9. When agent1 proposes a Task with edge `blocks → A` and `fields: {x: 1}`. Then RuleViolation `schema` (no fields allowed on `blocks`).
10. When agent1 proposes a Note with edge `supersedes → H1`. Then RuleViolation `reserved_edge_type`.
11. Given the file written by scenario 1. When it is loaded and dumped again. Then the bytes are identical, and its keys appear in the order of `data-model.md` §2 with `budget: null`, `usage: null`, `tags: []`, `priority: null`, `lease: null`.
