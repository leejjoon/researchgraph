# link
1. Given tasks A and B active. When agent1 links `blocks A → B`. Then an edge file exists with author agent1, `show B` reports blocked, and the commit message starts with `rg: link ed-`.
2. Given tasks A and B with `blocks A → B`. When agent1 links `blocks B → A`. Then RuleViolation `cycle`.
3. When agent1 links `answers` from a Task to a Question. Then RuleViolation `edge_endpoints`.
4. When agent1 links `blocks` with `fields: {x: 1}`. Then RuleViolation `schema` (no fields allowed on `blocks`).
5. Given `blocks A → B` already exists and is not retired. When anyone links `blocks A → B` again. Then RuleViolation `duplicate_edge`.
6. When anyone links `supersedes`, `explains` or `instructs`. Then RuleViolation `reserved_edge_type`.
7. Given Claim C1 proposed and Result R1. When alice links `cites C1 → R1`. Then `evidence C1` lists R1 and C1 can now be approved.
