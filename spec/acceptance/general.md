# general (every write)
1. Given any project. When any write names an id that does not exist. Then RuleViolation `not_found`, no files written, no commit.
2. Given a read-only project. When any write is attempted. Then RuleViolation `read_only`.
3. Given `profile.yaml` pinning version "9.0" of `research` and an installed profile at "1.0". When the project is opened. Then `profile_too_new`, exit code 2.
4. Given agent1. When agent1 calls `reject`, `instruct`, `expand`, `budget`, `retire` or `import`. Then RuleViolation `human_only_approval`.
5. Given a write that passes validation, following an earlier `attach --no-commit`. When a file rename fails mid-write. Then every file already written is restored to its pre-write bytes, the earlier attach's artifact record survives, `write_failed` is raised, and `git status` shows exactly the earlier uncommitted change.
6. Given two processes in one checkout claiming T1 at once. Then exactly one succeeds and the other gets `lease_held`.
7. Given any write with `--no-commit`. Then the files change, the index reflects them, `git status` shows them modified, and no event hook fires.
8. Given a sequence of random valid and invalid writes (hypothesis). Then after each one, deleting the index and rebuilding gives identical results for every query (I8), and I1–I7 hold.
