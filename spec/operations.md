# Operations (normative)

Every operation exists in three doors with the same name and arguments: Python (`Project.<op>(...)`), CLI (`rg <op> ...`, `--json` for machine output), MCP (tool `<op>`, v0.2). Each is marked **read** or **write**. A project opened read-only refuses every write with `RuleViolation(rule="read_only")`.

Every call carries an `actor` (`data-model.md` §2.1) and an optional declared `role`. The CLI derives a human actor from `git config user.name` and `user.email` unless `--as-agent NAME[:MODEL[:VERSION]]` is given; `--role ROLE` declares a role for the call. Roles are self-declared; the profile only lists which role names exist and which gates they satisfy. Every call also takes `now` (CLI `--now`, default wall clock) which is the timestamp written and the clock for the predicates in `data-model.md` §6.

Errors are `RuleViolation{rule: str, message: str, ids: [str]}`; the CLI prints them as JSON on stderr and exits 2. Nothing is written on a validation error; the only exceptions are `write_failed` after a mid-write or commit failure (`store-layout.md`, Writes). When several rules apply, the shared rules are checked first in the order listed above (`read_only`, `clock_skew`, `not_found`, `profile_too_new`, `human_only_approval`, `wrong_role`, `unsupported_by_profile`), then the row's rules in the order listed, except where a row says otherwise; `artifact_path` precedes `artifact_missing`. Edges may be created to or from retired nodes; no rule forbids it. Every write may raise, in addition to the rules listed per row: `read_only`; `clock_skew` (`store-layout.md`, `--as-of`); `not_found` (an id argument does not exist); `profile_too_new`; `human_only_approval` (an agent calls a write whose *Allowed to* is `human`); `wrong_role` (the declared role is not one the operation or gate accepts); `unsupported_by_profile` (`data-model.md` §8); `write_failed` (`store-layout.md`, Writes).

Where an operation records something, the record is the `approvals` entry defined in `data-model.md` §2.2, and generated nodes are filled per §2.3.

## Writes

| Op | Arguments | Effect | Allowed to (default) | Rules it can raise |
| --- | --- | --- | --- | --- |
| `init` | `path, profile` | Create the layout in `store-layout.md`, run `git init` if `path` is not a repository, write `profile.yaml` and `.gitignore`, make the first commit `rg: init`. | human | `already_initialised`, `unknown_profile` |
| `propose` | `type, title, body?, fields?, edges?: [{type, to|from, fields?}]` | Create node in `proposed` (or `active` if the profile has no activate gate for the type). Create the listed edges, each incident to the new node. | human, agent | `unknown_type`, `reserved_edge_type`, `schema`, `edge_endpoints`, `cycle`, `duplicate_edge` |
| `link` | `type, from, to, fields?` | Create one edge between two existing nodes. `supersedes`, `explains` and `instructs` are reserved for their operations. | human, agent | `reserved_edge_type`, `edge_endpoints`, `schema`, `cycle`, `duplicate_edge` |
| `approve` | `node, what?=activate, note?` | Record approval; if the activate gate for the node's type is satisfied, `proposed → active`. `what` must be `activate`. A second approval by the same actor on the same node raises `already_approved`. | human; a gate with `agents_allowed` admits agents, otherwise an agent gets `human_only_approval` (checked after `not_proposed`) | `wrong_role`, `not_proposed`, `already_approved`, `unknown_what` |
| `reject` | `node, reason` | `proposed → retired`; retire its non-retired edges; reason recorded. | human | `not_proposed` |
| `supersede` | `node, title?, body?, fields?, inherit_edges?: bool=true, instruction?` | Create the replacement (same type, §2.3), create `supersedes replacement → node`, set the old node `retired` and record `superseded`, retire each *transferable* edge (§3) and if `inherit_edges` recreate it from the replacement; leave every other edge untouched. If `instruction` is given, also create an Instruction node with `instructs → replacement`. | human for any type; agent for Task and Result only | `agent_cannot_supersede_type`, `terminal_status`, `running`, `cycle`, `schema` |
| `instruct` | `node, text` | Create an Instruction node (§2.3) and `instructs → node`. `node` must be `proposed` or `active`. | human | `already_ran` |
| `claim` | `node, lease_seconds=7200` | If `ready(node)`: set `status = running` and `lease = {actor, acquired_at: now, expires_at: now + lease_seconds}`, replacing any expired lease; record `claim`. | human, agent | `lease_held` (running with an unexpired lease; checked before `not_ready`), `not_ready`, `negative` (`lease_seconds < 1`) |
| `renew` | `node, lease_seconds=7200` | `expires_at = max(expires_at, now + lease_seconds)`; record `renew`. Holder only, even after expiry. | lease holder | `not_running`, `not_lease_holder`, `negative` (`lease_seconds < 1`) |
| `release` | `node, note?, usage?` | `running → active`, clear lease, add `usage`, record `release`. | lease holder or any human | `not_running`, `not_lease_holder`, `negative` |
| `complete` | `node, summary, artifacts?: [path], usage?` | `running → done`; for each path (relative to project root, under `artifacts/<node id>/`, existing) append an artifact record with its sha256 and size and stage the file in the commit; add `usage`; clear lease; record `complete`. | lease holder | `not_running`, `not_lease_holder`, `artifact_path`, `artifact_missing`, `negative` |
| `fail` | `node, summary, step?, check?, measured?, error?, usage?` | `running → failed`; create a Failure node (§2.3) and `explains → node`; add `usage`; clear lease; record `fail`. | lease holder | `not_running`, `not_lease_holder`, `negative` |
| `expand` | `nodes: [id], hint?` | Create one Expansion task (§2.3). The core never executes it. | human | `no_targets` |
| `budget` | `node, tokens?, compute_seconds?` | Set `budget` to exactly the given dimensions (an omitted dimension is removed); record `budget`. Zero means stop. | human | `negative` |
| `retire` | `node, reason` | `proposed|active|failed → retired` without replacement; retire its non-retired edges; record `retire`. | human | `terminal_status`, `running` |
| `attach` | `node, artifacts: [path]` | Append artifact records (as `complete` does) to any non-retired node, staging the files. | human, agent | `terminal_status` (retired only), `artifact_path`, `artifact_missing` |
| `tag` | `node, add?: [str], remove?: [str], priority?: int|null` | Edit `tags` and `priority`; `priority: null` clears it. No record. | human, agent | — |
| `import` | `archive, path` | Into an empty or non-existent directory: create `init`'s layout (without its commit) pinning the archive's profile, write `graph/`, `.researchgraph/history.jsonl` and any artifacts, then make one commit `rg: import`. | human | `not_empty`, `bad_archive`, `profile_too_new` |

Budget is enforced only as a query (`where --over-budget`) and a `ready` exclusion. The core does not meter tokens; consumers pass `usage` to `complete`, `fail` or `release`, and each call adds to the stored totals. Operation count: eighteen writes (`export` is a read).

The commit for a write touches only the files that write created or changed. Its message is `rg: <op> <id>` where `<id>` is the node argument for operations that take one (for `supersede` the old node, for `fail` the failed node), the new node's id for `propose` and `expand`, the edge id for `link`, the node for `attach`, and empty for `init` and `import`.

## Reads

| Query | Returns |
| --- | --- |
| `show <id>` | the node or edge; for a node also its non-retired incident edges, the Instruction nodes that `instructs` it, its pins, derived flags `blocked`, `ready`, `stale`, `lease_expired`, `over_budget`, and profile warnings such as `non_discriminating` |
| `ready` | nodes with `ready(n)` in `ready` order (§6) |
| `blocked` | nodes with `blocked(n)` and the ids blocking them, in `ready` order |
| `queue` | `proposed` nodes whose type has an activate gate, with the gate's roles and the approvals so far, oldest first |
| `history <id>` | the `supersedes` chain in both directions (following `supersedes` edges whether or not retired), then every `approvals` record, artifact record and status interval of every node in the chain, each with the commit that introduced it, oldest first |
| `evidence <claim>` | the closure of `claim` over non-retired `cites` then `derived-from` edges (every node reachable by any sequence of those edges), with artifacts; `claim` itself excluded |
| `stale` | claims with `stale(c)` and, for each, the responsible node: the head of the `supersedes` chain from the superseded input (from the input, follow its non-retired incoming `supersedes` edge to the source, and repeat until a node with no such edge) |
| `weakest <claim>` | the `cites` targets sorted ascending by the tuple `(has no superseded input, fields.runs or 0, fields.held_out or false, id)`: superseded-input results first, then fewer runs, then no held-out flag. A result has a superseded input when it, or any node reachable from it via non-retired `derived-from` or `cites` edges, has a non-retired incoming `supersedes` edge |
| `predictions <hypothesis>` | its incoming non-retired `tests` edges with source node, `expected`, `discriminating`, and the source's status, oldest first |
| `matrix <question>` | one row per source of a non-retired `tests` edge into any member of the exclusion group (Prediction or Task), one column per member (both in `created` order), cell = `expected` or empty, with `non_discriminating` per row (§6) |
| `alternatives <hypothesis>` | the union over every question it answers of the other members of that exclusion group, each with status and its non-retired `supports`/`contradicts` claims; `created` order |
| `since <time>` | nodes whose `created`, `updated`, any `approvals[].at` or `artifacts[].added_at` is after `time`, and edges whose `created` or `retired_at` is after `time` |
| `where <filter>` | `--type`, `--status`, `--author NAME`, `--field k=v` (string equality on `fields.k`), `--tag t`, `--text "..."` (title and body substring, case-insensitive), `--over-budget`; filters conjoin; `created` order |
| `notes <id>` | Note nodes with a non-retired `about` edge to the node, newest first |
| `subtree <id>` | the nodes in `subtree(id)` with status counts and summed usage |
| `digest` | counts and lists of: `running`, `ready`, `blocked`, `awaiting` (the `queue`), `failed_24h` (nodes whose status became `failed` within the 24 hours before `now`), `claims` (non-retired Claims) |
| `export` | `out, include_artifacts?: bool = false, since?` — write a tar.zst containing `graph/`, `.researchgraph/profile.yaml`, `.researchgraph/history.jsonl` (the status intervals), and optionally `artifacts/`. With `since`, only nodes and edges changed after `since` (as `since` defines changed), plus both endpoints of every included edge, then every node reachable from the included set by `supersedes`, `cites` and `derived-from`, together with the edges traversed. With `since`, `history.jsonl` carries only the included nodes' intervals. The archive is POSIX ustar with a member for every directory and file, in sorted path order, mtime 0, uid and gid 0, empty uname and gname, mode 0644 (0755 for directories), compressed with zstd level 3, so the bytes are deterministic across runs and machines. |

Every read accepts `--as-of <time|commit>`; `store-layout.md` says what is reconstructed. A commit maps to its committer time.

## CLI conventions
- `rg --project PATH` or run inside the project.
- `--json` emits one JSON object (or array) on stdout; without it, a human table. Node and edge objects use the YAML key names; `show` adds `edges`, `instructions`, `pins`, `flags` and `warnings`; `history` is a list of `{kind: interval|approval|artifact|supersedes, node, commit, at, ...}` oldest first; `matrix` is `{rows: [{source, cells: {hypothesis: expected}, non_discriminating}], columns: [...]}`; `subtree` is `{nodes: [...], status_counts: {...}, usage: {...}}`. `ready`, `blocked`, `queue`, `stale`, `evidence` and `digest` return `{items: [...]}` with node objects plus the extra keys their rows name (`blocking`, `gate`, `approvals`, `responsible`, `artifacts`, counts). These shapes are frozen at T15 for the v0.2 wrappers.
- `--commit/--no-commit` on writes (default commit). Event hooks fire only after a commit.
- `--read-only` opens the project read-only for the whole invocation.
- `--as-agent NAME[:MODEL[:VERSION]]`, `--role ROLE`, `--now TIMESTAMP`.
- Structured arguments are JSON: `--fields '{"k": 1}'`, `--usage '{"tokens": 10}'`; `--edge '{"type": "tests", "to": "hy-..."}'` repeatable; `--artifact PATH` repeatable; `--tokens N --compute-seconds M` for `budget`.
- Exit codes: 0 ok, 2 RuleViolation (including `profile_too_new`), 3 project not found or invalid.
