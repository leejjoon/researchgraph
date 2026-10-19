# notes, pins, part-of, addresses
1. Given H1 active. When agent1 proposes a Note "looks degenerate with H2" with edge `about → H1`. Then `notes H1` lists it; H1's immutable keys are unchanged.
2. Given Result R1 with artifact `fig2.png`. When alice proposes edge `pins H1 → R1 {artifact: fig2.png}`. Then `show H1` lists the pin. When the artifact path is not among R1's artifacts. Then RuleViolation `schema`.
3. Given Task T1 with `addresses → P1` and `tests P1 → H1`. Then `subtree H1` contains P1 and T1; `subtree P1` contains T1 only.
4. Given Question Q2 with `part-of → Q1`. Then `subtree Q1` contains Q2 and everything under it; proposing `part-of Q1 → Q2` raises `cycle`.
5. Given budget tokens 1000 on H1 and T1 completed with usage 1200. Then `where --over-budget` lists H1 and no task in `subtree H1` is ready.
