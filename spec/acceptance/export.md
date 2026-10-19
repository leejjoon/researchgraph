# export
1. Given any project with ≥ 50 nodes and history. When alice exports to `out.tar.zst` and it is imported into an empty directory. Then `graph/` and `.researchgraph/profile.yaml` are byte-identical and every query returns the same result.
2. When exported with `--since <t>`. Then only nodes/edges changed after `t` are included, plus everything they reference by `supersedes`, `cites`, `derived-from`.
