# notes, pins, part-of, addresses
1. Given H1 active. When agent1 proposes a Note "looks degenerate with H2" with edge `about → H1`. Then `notes H1` lists it; H1's immutable keys are unchanged.
2. Given Result R1 with artifact `artifacts/<R1>/fig2.png` recorded via `attach`. When alice links `pins H1 → R1 {artifact: "artifacts/<R1>/fig2.png"}`. Then `show H1` lists the pin. When the artifact path is not among R1's artifacts. Then RuleViolation `schema` (profile validator `pin_artifact_exists`).
3. Given Task T1 with `addresses → P1` and `tests P1 → H1`. Then `subtree H1` is {H1, P1, T1} and `subtree P1` is {P1, T1}.
4. Given Question Q2 with `part-of → Q1`. Then `subtree Q1` contains Q2 and everything under it; linking `part-of Q1 → Q2` raises `cycle`.
5. Given budget tokens 1000 on H1 and T1 completed with usage 1200. Then `where --over-budget` lists H1 and no task in `subtree H1` is ready.
6. Given a Result R1 proposed by agent1 with no artifacts and a file `artifacts/<R1>/fig2.png`. When agent1 attaches it. Then R1 has one artifact record with the file's sha256, `since` lists R1, and the commit includes the file.
