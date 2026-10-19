# attach
1. Given Result R1 active and a file `artifacts/<R1>/fig2.png`. When agent1 attaches it. Then `artifacts` has one record `{path, sha256, bytes, added_at, added_by: agent1}` and the commit message is `rg: attach <R1>`.
2. When agent1 attaches a path that does not exist. Then RuleViolation `artifact_missing`.
3. When agent1 attaches `artifacts/<other id>/x.png` or `data/x.png`. Then RuleViolation `artifact_path`.
4. Given R1 retired. When anyone attaches. Then RuleViolation `terminal_status`.
5. Given Task T1 done. When alice attaches a second file. Then success; `artifacts` has two records in order of addition.
