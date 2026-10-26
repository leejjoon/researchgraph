# init
1. Given an empty directory. When alice runs `rgraph init --profile research`. Then `graph/nodes`, `graph/edges`, `.researchgraph/profile.yaml` (`name: research`, `version: "1.0"`), and `.gitignore` containing `.researchgraph/index.sqlite` and `.researchgraph/lock` exist, and `git log` has one commit `rg: init`.
2. Given an existing git repository with commits. When alice runs `rgraph init`. Then the same files are added in a new commit and existing history is untouched.
3. Given an initialised project. When alice runs `rgraph init` again. Then RuleViolation `already_initialised`.
4. When alice runs `rgraph init --profile widgets`. Then RuleViolation `unknown_profile`.
