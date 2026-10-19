# propose
1. Given the research profile. When alice proposes a Hypothesis "H1". Then a file `graph/nodes/hy-*.yaml` exists with status `proposed`, author alice, and the commit message starts with `rg: propose`.
2. Given the same. When agent1 proposes a Task "T1" with no edges. Then status is `active` (Task has no gate) and `ready` lists it.
3. Given H1 proposed. When agent1 proposes a Prediction "P1" with edge `tests → H1`. Then the edge file exists and `show P1` lists it.
4. When anyone proposes a node of unknown type "Widget". Then RuleViolation `unknown_type`, no files written.
5. When alice proposes a Hypothesis whose `fields` violate the schema. Then RuleViolation `schema`.
6. Given tasks A and B with `blocks A → B`. When agent1 proposes an edge `blocks B → A`. Then RuleViolation `cycle`.
7. When agent1 proposes an edge `answers` from a Task to a Question. Then RuleViolation `edge_endpoints`.
8. When agent1 proposes a Prediction with edge `tests → H1` and no `expected` field. Then RuleViolation `schema` (research profile requires it).
9. When agent1 proposes edge `blocks` with `fields: {x: 1}`. Then RuleViolation `schema` (no fields allowed on `blocks`).
