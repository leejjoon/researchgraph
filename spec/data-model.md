# Data model (normative)

## 1. Project
A project is a git repository containing a `graph/` directory and a `.researchgraph/` directory (see `store-layout.md`). It pins exactly one profile.

## 2. Node
A node is a YAML mapping with these keys, always written in this order. Keys marked **immutable** never change after the node file is first committed; a validator refuses any change to them.

| Key | Type | Immutable | Notes |
| --- | --- | --- | --- |
| `id` | string | yes | `<prefix>-<8 chars>` matching `^[a-z]{2,4}-[a-z2-7]{8}$`; prefix is the profile's code for the type (e.g. `hy`, `ta`, `cl`); the 8 chars are random from the lowercase RFC 4648 base32 alphabet `a-z2-7`. Never content-derived. On collision with an existing file, regenerate. |
| `type` | string | yes | One of the profile's node types. |
| `title` | string | yes | ≤ 200 chars, single line; violations raise `schema`. |
| `body` | string | yes | Markdown. May be empty. |
| `author` | Actor | yes | See *Actor* below. |
| `created` | RFC 3339 timestamp | yes | UTC, whole seconds, `Z` suffix. |
| `fields` | mapping | yes | Profile-defined extras; validated against the profile's JSON Schema for the type (closed: no undeclared keys). Schema defaults are materialised at write time. Optional keys with no value are omitted from the file and read as null; an explicit null in a `fields` argument is dropped before validation. Keys are stored in sorted order (`store-layout.md`). |
| `status` | enum | no | `proposed | active | running | done | failed | retired` |
| `updated` | RFC 3339 | no | Equals `created` until a mutable key changes. |
| `approvals` | list | no | Append-only event log of `{actor, role, at, what, note}` (section 2.2). |
| `artifacts` | list | no | Append-only list of `{path, sha256, bytes, added_at, added_by}`, written by `complete` and `attach`. `path` is relative to the project root and must lie under `artifacts/<this node's id>/`. Recording the same path again appends a new record. |
| `budget` | mapping or null | no | `{tokens?: int, compute_seconds?: int}`; limits for this node's subtree (section 6). `null` when no dimension is set; `{}` is never written. |
| `usage` | mapping or null | no | `{tokens?: int, compute_seconds?: int}`; running totals. `complete`, `fail` and `release` **add** the amounts they are given. Values are non-negative. `null` until a dimension is recorded; `{}` is never written. |
| `tags` | list of string | no | Free labels for filtering. Sorted, unique. |
| `priority` | int or null | no | Higher first in `ready`; null sorts after every integer. |
| `lease` | mapping or null | no | `{actor: Actor, acquired_at, expires_at}`; non-null iff `status == running`. |

`blocked` and `ready` are **not** keys. They are derived (section 6).

### 2.1 Actor
Every stored reference to a person or agent (`author`, `lease.actor`, `approvals[].actor`, `artifacts[].added_by`) is the mapping `{kind: human|agent, name: str, email?: str, model?: str, version?: str}`, keys in that order, optional keys omitted. Two actors are the same actor iff `kind` and `name` are equal; this is the test behind `not_lease_holder`. `role` is **not** stored on the actor; it is recorded per approval record. For git commits a human actor's author line is its git identity and an agent's is `NAME <NAME@agents.researchgraph>` (`store-layout.md`, Writes). Roles are self-declared per call (`operations.md`); the trust model is declared identity.

### 2.2 Approval records
`approvals` is the node's event log. Each operation listed below appends exactly one record `{actor, role, at, what, note}`:

| `what` | Appended by | `note` holds |
| --- | --- | --- |
| `approve` | `approve` | the note, or null |
| `reject` | `reject` | the reason |
| `retire` | `retire` | the reason |
| `claim` | `claim` | null |
| `renew` | `renew` | null |
| `release` | `release` | the note, or null |
| `complete` | `complete` | the summary |
| `fail` | `fail` | the summary |
| `superseded` | `supersede` (on the old node) | the replacement's id |
| `budget` | `budget` | the new budget as `tokens=N compute_seconds=M`, omitting absent dimensions; `none` when cleared |

`role` is the role the actor declared for the call, or null. `at` is the operation's timestamp. Null `role` and `note` are omitted from the file and read as null. `tag` does not append a record.

### 2.3 Generated nodes
Operations that create a node other than `propose` fill it as follows. A computed `title` is truncated to 200 chars; an explicit `title` argument is validated as in `propose` and raises `schema` when over-long or multi-line.

| Operation | Type | `title` | `body` | `fields` |
| --- | --- | --- | --- | --- |
| `supersede` | same as old node | argument, else old title | argument, else old body | argument, else old fields |
| `instruct` | Instruction | first line of `text` | `text` | `{}` |
| `fail` | Failure | `Failure: <old title>` | `summary` | `{step, check, measured, error}` as given, absent ones omitted |
| `expand` | Task | `Expand: <titles of targets, comma-separated>` | `hint`, else empty | `{kind: expansion, targets: nodes}` |

Each has `author` = the calling actor, `created` = `updated` = `now`, and is created `proposed` if the profile has an activate gate for its type, else `active`. `supersede` and `fail` may create types the profile must declare; if the profile lacks `Failure` or `Instruction`, `fail` and `instruct` raise `unsupported_by_profile`.

## 3. Edge
An edge is a YAML mapping `{id, type, from, to, author, created, fields, retired, retired_at}`, keys in that order. `id` is `ed-<8 chars>` with the same alphabet as node ids. All keys except `retired` and `retired_at` are immutable. Edges are directed from `from` to `to`. `retired_at` is null until the edge is retired.

`fields` is an immutable mapping validated against the profile's JSON Schema for that edge type. Every schema is closed: an empty schema `{}` means no keys are allowed. It carries relation-specific data, for example what a hypothesis predicts for a given test. A node may have any number of edges of the same type: one Prediction may `tests` several hypotheses, one Claim may `supports` and `contradicts` several. Two non-retired edges with the same `type`, `from` and `to` are a `duplicate_edge`.

### Core edge types (every profile has these; profiles may add, never remove)

`inherit` says what `supersede` does with the old node's edges (decision 006). An edge incident to the superseded node X is *transferable* when its type is `both`, or its type is `outgoing` and the edge's `from` is X. `supersede` retires each transferable edge and (if `inherit_edges`) recreates it with the replacement in X's place; every other edge is left untouched, non-retired, still pointing at the retired X. Recreated edges copy `fields`, have the superseding actor as `author`, `created` = `now`, and a new id.

| Type | from → to | inherit | Meaning |
| --- | --- | --- | --- |
| `blocks` | any → any | both | `to` may not become ready until `from` is `done`. |
| `tests` | Prediction or Task → Hypothesis | both | the source tests the target. Research profile fields: `expected: str` (what the hypothesis predicts for this test), `discriminating: bool`. |
| `supersedes` | X → X (same type) | none | source replaces target; target becomes `retired`. Created only by `supersede`; never retired by a later supersession. |
| `derived-from` | any → any | outgoing | source was produced from target. |
| `supports`, `contradicts` | Claim → Hypothesis | outgoing | direction of bearing, about the specific hypothesis evaluated. |
| `discovered-from` | any → any | outgoing | source was found while working on target. |
| `instructs` | Instruction → any | both | the instruction applies to the target. Created only by `instruct` and `supersede`. |
| `explains` | Failure → any | outgoing | the failure explains why the target failed. Created only by `fail`. |
| `cites` | Claim → Result or Reference | outgoing | evidence relation; `evidence`, `stale`, `weakest` follow these. Research profile fields: `{artifact?: path, locator?: str, sha256?: str}`. |
| `answers` | Hypothesis → Question | both | membership of the question's exclusion group. |
| `part-of` | any → any | both | containment; defines subtrees for budgets and progress. Question → Question expresses sub-questions. |
| `addresses` | Task → Prediction | both | the task carries out the prediction's test. |
| `about` | Note → any | outgoing | a comment or annotation on a node. |
| `pins` | Hypothesis → Result | outgoing | highlighted evidence; fields `{artifact: path}`. |
| `duplicate-of`, `relates-to` | any → any | both | housekeeping; no semantics in the core. |

Profile-added edge types default to `inherit: outgoing`.

## 4. Invariants (every write must preserve all of these)
- **I1 Acyclicity**: the graph restricted to non-retired `blocks`, `derived-from`, `supersedes`, `cites` and `part-of` edges has no directed cycle.
- **I2 Append-only**: no node or edge file is ever deleted; no immutable key of an existing node or edge changes; `approvals` and `artifacts` only grow and existing records never change; a retired edge never becomes unretired.
- **I3 Single supersession**: a node has at most one non-retired incoming `supersedes` edge.
- **I4 Lease consistency**: `lease` is non-null iff `status == running`; `expires_at > acquired_at`.
- **I5 Edge endpoints**: both endpoints exist; the pair of types is allowed for that edge type by the profile; `fields` validates against the edge type's schema; no `duplicate_edge`.
- **I6 Status transitions**: only those in section 5 occur.
- **I7 Permission**: the actor's kind and declared role allow the operation per `operations.md` and the profile's gates.
- **I8 File/index agreement**: after any write, and after any sequence of writes including failed ones, rebuilding the index from files yields identical query results for every query, including `--as-of`.

## 5. Lifecycle
```
proposed --approve--> active        (immediately active at creation if the profile has no activate gate for this type)
proposed --reject---> retired       (human; reason recorded; edges retired)
active   --claim----> running       (only if ready, section 6)
running  --claim----> running       (only if lease_expired; new holder)
running  --renew----> running       (lease extended, never shortened; holder only)
running  --release--> active
running  --complete-> done
running  --fail-----> failed
proposed|active|done|failed --supersede-> retired   (the replacement is created proposed or active per gate)
proposed|active|failed      --retire----> retired   (human; reason recorded; no replacement)
```
No other transitions. `done`, `failed` and `retired` are terminal, except that `done` and `failed` may be superseded and `failed` may be retired. `supersede` and `retire` on a `running` node raise `running`; on a `retired` node, `terminal_status`.

## 6. Derived predicates
All predicates take an evaluation time `now`; the CLI uses the wall clock and tests fix it.

- `lease_expired(n)`: `n.status == running` and `now >= n.lease.expires_at`.
- `blocked(n)`: `n.status in (active, running)` and there exists a non-retired `blocks` edge `m → n` with `m.status != done`.
- `ready(n)`: (`n.status == active` or `lease_expired(n)`) and `n.type` is claimable in the profile and not `blocked(n)` and no node in `{n} ∪ ancestors(n)` (see `subtree`) has `over_budget`. Gates do not enter: an active node has already passed its activate gate.
- `ready` order: `priority` descending with null last, then `created` ascending, then `id` ascending.
- `stale(c)`: `c.type == Claim`, `c.status != retired`, and some node reachable from `c` via non-retired `cites` or `derived-from` edges has a non-retired incoming `supersedes` edge.
- `exclusion_group(q)`: all Hypothesis nodes with a non-retired `answers` edge to Question `q`. A hypothesis may answer several questions and then belongs to several groups.
- `subtree(n)`: `n` plus every node reachable from `n` by following non-retired `part-of`, `tests`, `addresses` or `derived-from` edges *backwards* (i.e. nodes that are part of, test, address or derive from `n`), transitively, as a set (cycles through `tests` or `addresses` terminate because visited nodes are not revisited). `ancestors(n)` is the inverse: every `m` with `n ∈ subtree(m)`, excluding `n`. Progress counts use `subtree(n)` as defined. For `over_budget` and the `usage` sum reported by `subtree <id>`, the same traversal is taken over *all* `part-of`, `tests`, `addresses` and `derived-from` edges **including retired ones**, so a retired or superseded descendant still contributes its usage to every ancestor it ever belonged to (decision 010). `ancestors(n)` uses non-retired edges.
- `over_budget(n)`: `n.budget` is set and, for some dimension present in the budget, the sum of `usage` over the accounting traversal of `n` (above; missing values count as 0) is **≥** the limit. A budget of 0 therefore stops work immediately.
- `predictions(h)`: for Hypothesis `h`, the non-retired `tests` edges into it, each with its source node and `fields.expected`; the matrix for a question is `predictions` over its exclusion group. Where two non-retired `tests` edges share `from` and `to` (impossible under I5) or a hypothesis lacks a prediction, the cell is empty.
- `non_discriminating(p)`: for a `tests` source node `p` (Prediction or Task) with two or more non-retired `tests` edges, every edge's `expected` is equal. A prediction with one edge is not flagged. This is the single definition used by `show`, `matrix` and the research profile's warning.

## 7. Competing hypotheses
A Question node has a profile-defined boolean field `closed` (default false), set at creation. The group is informational: no operation retires a hypothesis because of group membership. Auto-proposing the last member of a closed group is **reserved**: no profile attribute declares it in v0.1 and the core does not do it.

## 8. Profiles and reserved vocabulary
A profile declares: node types with JSON Schemas for `fields`, an id prefix and a `claimable` flag each; edge types (core ones may be given a `fields` schema; additional ones also declare allowed endpoint type pairs and `inherit`); activate gates (`{node_type, roles, agents_allowed}`); validators; an optional expansion hook; renderers; event hooks; agent guidance text. A project pins `name` and `version` in `.researchgraph/profile.yaml`. Two profiles ship with the core: `research` and `tasks` (`profiles.md`).

The core recognises these names when a profile declares them, and behaves as stated when it does not. No other type or field name has meaning in the core.

| Name | Used by | When the profile does not declare it |
| --- | --- | --- |
| node `Task` | `expand` | `expand` raises `unsupported_by_profile` |
| node `Instruction` | `instruct`, `supersede(instruction=…)` | both raise `unsupported_by_profile` |
| node `Failure` | `fail` | `fail` raises `unsupported_by_profile` |
| node `Claim` | `stale`, `evidence`, `weakest`, `digest.claims` | those queries return empty |
| node `Hypothesis`, `Question`, `Prediction` | `exclusion_group`, `alternatives`, `predictions`, `matrix` | those queries return empty |
| node `Note` | `notes` | returns empty |
| node `Task`, `Result` | `supersede`'s agent permission | an agent may supersede only those two types; a profile without them gives agents no supersede rights |
| node `Result`, `Reference` | endpoints of core `cites` and `pins`; `weakest` | an endpoint rule naming an undeclared type never matches (I5 refuses the edge); `weakest` returns empty |
| field `Question.closed` | section 7 | treated as false |
| field `Result.runs: int`, `Result.held_out: bool` | `weakest` | treated as 0 and false |
| field `Task.kind`, `Task.targets`, `Task.command` | `expand`; the reference runner | `expand` raises `unsupported_by_profile` if the Task schema rejects `kind` or `targets` |
