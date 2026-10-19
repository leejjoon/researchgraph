# instruct
1. Given T1 active. When alice instructs T1 "skip object 12811". Then an Instruction node with title and body "skip object 12811" and an `instructs` edge exist; `show T1` lists the instruction.
2. Given T1 running or done. When alice instructs. Then RuleViolation `already_ran`.
3. When agent1 instructs. Then RuleViolation `human_only_approval`.
4. Given a profile without an Instruction type. When alice instructs. Then RuleViolation `unsupported_by_profile`.
