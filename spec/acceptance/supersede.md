# supersede
1. Given H1 active with edges `tests P1 → H1` and `answers H1 → Q1`. When alice supersedes H1 with title "H1′". Then: new node H1′ proposed (Hypothesis has a gate) with H1's body and fields; edge `supersedes H1′ → H1`; H1 retired with an approvals record `what: superseded, note: <H1′ id>`; the two old edges retired; two new edges exist with H1′ in H1's place, author alice, same `fields`; immutable keys of H1 unchanged.
2. Given the same. When agent1 supersedes H1. Then RuleViolation `agent_cannot_supersede_type`.
3. Given Task T1 failed. When agent1 supersedes T1 with instruction "use 0.4 arcsec". Then T1′ active, an Instruction node with body "use 0.4 arcsec" exists with `instructs → T1′`, T1 retired.
4. Given H1 retired. When alice supersedes H1. Then RuleViolation `terminal_status`.
5. Given H1 superseded by H1′. When alice supersedes H1 again. Then RuleViolation `terminal_status` (H1 is retired, so I3 can never be violated through an operation).
6. `history H1′` shows H1 ← H1′ and `history H1` shows the same chain.
7. Given P2 with `tests → H3 {expected: "no detection"}` and `tests → H1 {expected: "weak absorbed"}`. When alice supersedes H1 with H1′. Then P2's edge to H3 is untouched, the edge to H1 is retired, and a new `tests P2 → H1′` exists with `fields.expected == "weak absorbed"`.
8. Given T1 running by agent1. When alice supersedes T1. Then RuleViolation `running`; T1's lease is unchanged.
9. Given Claim C1 active with `cites C1 → R1` and `supports C1 → H1`. When alice supersedes R1 with R1′. Then `cites C1 → R1` is **not** retired (an incoming edge of an `outgoing` type is left untouched), no `cites C1 → R1′` exists, `evidence C1` still lists R1, and `stale` lists C1 with `responsible == [R1′]`.
10. Given the same. When alice supersedes H1 with H1′. Then `supports C1 → H1` stays and `alternatives`/`show H1′` list no supporting claims until someone links `supports C1 → H1′`.
11. Given R0 superseded by R1, then R1 superseded by R2. Then `supersedes R1 → R0` is still non-retired, `history R2` shows R0 ← R1 ← R2, and a claim citing R0 is stale with `responsible == [R2]`.
12. Given Note N1 with `about → H1`. When alice supersedes H1. Then `about N1 → H1` is untouched (incoming edge of an `outgoing` type): `notes H1` still lists N1 and `notes H1′` is empty.
13. Given Claim C1 with `supports C1 → H1`. When alice supersedes C1 with C1′. Then `supports C1′ → H1` is created (outgoing from the superseded node) and `supports C1 → H1` is retired.
14. Given Failure F1 with `explains F1 → T1`. When alice supersedes F1 with F1′. Then the old `explains` edge is retired and `explains F1′ → T1` is inherited with unchanged fields. Direct `propose` or `link` creation of `explains` still raises `reserved_edge_type`.
