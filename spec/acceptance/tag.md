# tag
1. Given T1 and T2 ready, both created at the same time. When alice sets priority 5 on T2. Then `ready` lists T2 first. When alice clears T2's priority (`priority: null`). Then `ready` lists them in id order.
2. When agent1 adds tag `needs-review` to T1. Then `where --tag needs-review` lists T1; removing it empties the result. Tags are stored sorted and unique.
3. Tagging changes `updated` and nothing else: immutable keys and `approvals` are unchanged.
