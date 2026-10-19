# supersede
1. Given H1 active with edges `tests P1 → H1` and `answers H1 → Q1`. When alice supersedes H1 with title "H1′". Then: new node H1′ proposed (Hypothesis has a gate); edge `supersedes H1′ → H1`; H1 retired; the two old edges retired; two new edges exist with H1′ in H1's place; immutable keys of H1 unchanged.
2. Given the same. When agent1 supersedes H1. Then RuleViolation `agent_cannot_supersede_type`.
3. Given Task T1 failed. When agent1 supersedes T1 with instruction "use 0.4 arcsec". Then T1′ active, an Instruction node exists with `instructs → T1′`, T1 retired.
4. Given H1 retired. When alice supersedes H1. Then RuleViolation `terminal_status`.
5. Given H1 superseded by H1′. When alice supersedes H1 again. Then RuleViolation (I3 single supersession).
6. `history H1′` shows H1 ← H1′ and `history H1` shows the same chain.
7. Given P2 with `tests → H3 {expected: "no detection"}` and `tests → H1 {expected: "weak absorbed"}`. When alice supersedes H1 with H1′. Then P2's edge to H3 is untouched, the edge to H1 is retired, and a new `tests P2 → H1′` exists with `fields.expected == "weak absorbed"`.
