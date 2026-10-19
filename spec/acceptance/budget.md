# budget
1. Given H1 active. When alice sets budget tokens 1000. Then `show H1` reports it; `where --over-budget` is empty.
2. Given tasks under H1 whose `fields.tokens_used` sum to 1200. Then `where --over-budget` lists H1 and tasks under H1 are not ready.
3. When alice sets tokens 0 and compute 0. Then every ready task under H1 becomes not ready ("stop").
