# import
1. Given an archive from `export`. When alice imports it into an empty directory. Then the directory is an initialised project pinning the archive's profile, `graph/` and `.researchgraph/history.jsonl` are present, and there is one commit `rg: import`.
2. When alice imports into a non-empty directory. Then RuleViolation `not_empty`.
3. When the archive is not a researchgraph export. Then RuleViolation `bad_archive`.
4. When the archive pins a profile version newer than installed. Then RuleViolation `profile_too_new`.
