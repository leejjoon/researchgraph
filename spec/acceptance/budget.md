# budget
1. Given H1 active. When alice sets budget tokens 1000. Then `show H1` reports it, approvals gains `what: budget`, and `where --over-budget` is empty.
2. Given tasks under H1 whose `usage.tokens` sum to 1200. Then `where --over-budget` lists H1 and tasks under H1 are not ready.
3. Given tasks under H1 whose `usage.tokens` sum to exactly 1000. Then H1 is over budget (inclusive comparison).
4. When alice sets tokens 0 and compute 0. Then every ready task under H1 becomes not ready ("stop"), even with no usage recorded.
5. When alice sets tokens -1. Then RuleViolation `negative`.
6. Given budget `{tokens: 1000, compute_seconds: 10}`. When alice sets tokens 2000 only. Then `budget == {tokens: 2000}`.
