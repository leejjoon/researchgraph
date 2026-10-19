# Data model (normative)

## 1. Project
A project is a git repository containing a `graph/` directory and a `.researchgraph/` directory (see `store-layout.md`). It pins exactly one profile.

## 2. Node
A node is a YAML mapping with these keys. Keys marked **immutable** never change after the node file is first committed; a validator refuses any change to them.

| Key | Type | Immutable | Notes |
| --- | --- | --- | --- |
| `id` | string | yes | `<prefix>-<8 lowercase base32 chars>`; prefix is the profile's two-to-four-letter code for the type (e.g. `hy`, `an`, `cl`). Generated randomly; never content-derived. |
| `type` | string | yes | One of the profile's node types. |
| `title` | string | yes | ≤ 200 chars. |
| `body` | string | yes | Markdown. May be empty. |
| `author` | mapping | yes | `{kind: human|agent, name: str, model?: str, version?: str}` |
| `created` | RFC 3339 timestamp | yes | UTC. |
| `fields` | mapping | yes | Profile-defined extras; validated against the profile's JSON Schema for the type. Absent keys are null. |
| `status` | enum | no | `proposed | active | running | done | failed | retired` |
| `updated` | RFC 3339 | no | Changes only when a mutable key changes. |
| `approvals` | list | no | Append-only list of `{actor, role, at, what, note?}`. |
| `artifacts` | list | no | Append-only list of `{path, sha256, bytes, added_at, added_by}`. Paths are relative to the project root. |
| `budget` | mapping or null | no | `{tokens?: int, compute_seconds?: int}`; limits for this node's subtree (section 6). |
| `usage` | mapping or null | no | `{tokens?: int, compute_seconds?: int}`; written by `complete`, `fail`, `release`. |
| `tags` | list of string | no | Free labels for filtering. |
| `priority` | int or null | no | Higher first in `ready`; ties oldest first. |
| `lease` | mapping or null | no | `{actor, acquired_at, expires_at}`; non-null only while `status == running`. |

`blocked` and `ready` are **not** keys. They are derived (section 6).

## 3. Edge
An edge is a YAML mapping: `{id, type, from, to, author, created, fields, retired: bool, retired_at?}`. `id` is `ed-<8 chars>`. All keys except `retired` and `retired_at` are immutable. Edges are directed from `from` to `to`.

`fields` is an immutable mapping validated against the profile's JSON Schema for that edge type (empty schema if none). It carries relation-specific data, for example what a hypothesis predicts for a given test. A node may have any number of edges of the same type: one Prediction may `tests` several hypotheses, one Claim may `supports` and `contradicts` several.

### Core edge types (every profile has these; profiles may add, never remove)

| Type | from → to | Meaning |
| --- | --- | --- |
| `blocks` | any → any | `to` may not become ready until `from` is `done`. |
| `tests` | Prediction or Task → Hypothesis | the source tests the target. Research profile fields: `expected: str` (what the hypothesis predicts for this test), `discriminating: bool`. |
| `supersedes` | X → X (same type) | source replaces target; target becomes `retired`. |
| `derived-from` | any → any | source was produced from target. |
| `supports`, `contradicts` | Claim → Hypothesis | direction of bearing. |
| `discovered-from` | any → any | source was found while working on target. |
| `instructs` | Instruction → any | the instruction applies to the target. |
| `explains` | Failure → any | the failure explains why the target failed. |
| `cites` | Claim → Result, Reference or artifact-bearing node | evidence relation; `evidence`, `stale`, `weakest` follow these. Research profile fields: `{artifact?: path, locator?: str, sha256?: str}`. |
| `answers` | Hypothesis → Question | membership of the question's exclusion group. |
| `part-of` | any → any | containment; defines subtrees for budgets and progress. Question → Question expresses sub-questions. |
| `addresses` | Task → Prediction | the task carries out the prediction's test. |
| `about` | Note → any | a comment or annotation on a node. |
| `pins` | Hypothesis → Result | highlighted evidence; fields `{artifact: path}`. |
| `duplicate-of`, `relates-to` | any → any | housekeeping; no semantics in the core. |

## 4. Invariants (every write must preserve all of these)
- **I1 Acyclicity**: the graph restricted to non-retired `blocks`, `derived-from`, `supersedes`, `cites` and `part-of` edges has no directed cycle.
- **I2 Append-only**: no immutable key of an existing node or edge changes; `approvals` and `artifacts` only grow.
- **I3 Single supersession**: a node has at most one non-retired incoming `supersedes` edge.
- **I4 Lease consistency**: `lease` is non-null iff `status == running`; `expires_at > acquired_at`.
- **I5 Edge endpoints**: both endpoints exist; the pair of types is allowed for that edge type by the profile; `fields` validates against the edge type's schema.
- **I6 Status transitions**: only those in section 5 occur.
- **I7 Permission**: the actor's kind and role allow the operation per `operations.md` and the profile's gates.
- **I8 File/index agreement**: after any write, rebuilding the index from files yields identical query results.

## 5. Lifecycle
```
proposed --approve--> active        (immediately active at creation if the profile requires no gate for this type)
proposed --reject---> retired
active   --claim----> running       (only if ready, section 6)
running  --renew----> running       (lease extended; holder only)
running  --release--> active
running  --complete-> done
running  --fail-----> failed
any      --supersede-> retired      (the replacement is created proposed or active per gate)
active|proposed|failed --retire--> retired   (human; reason recorded; no replacement)
```
No other transitions. `done`, `failed`, `retired` are terminal except that `failed` and `done` may be superseded.

## 6. Derived predicates
- `blocked(n)`: `n.status == active` and there exists a non-retired `blocks` edge `m → n` with `m.status != done`.
- `ready(n)`: `n.status == active` and not `blocked(n)` and all gates the profile requires before running are satisfied.
- `stale(c)`: `c.type == Claim` and some node reachable from `c` via non-retired `cites` or `derived-from` edges has a non-retired incoming `supersedes` edge.
- `exclusion_group(q)`: all Hypothesis nodes with a non-retired `answers` edge to Question `q`.
- `subtree(n)`: `n` plus every node reachable from `n` by following non-retired `part-of`, `tests`, `addresses` or `derived-from` edges *backwards* (i.e. nodes that are part of, test, address or derive from `n`), transitively. Budgets, `usage` sums and progress counts are computed over `subtree(n)`.
- `over_budget(n)`: `n.budget` set and the sum of `usage` over `subtree(n)` exceeds it in any dimension. A node is not ready if any ancestor's `over_budget` holds.
- `predictions(h)`: for Hypothesis `h`, the non-retired `tests` edges into it, each with its source node and `fields.expected`; the matrix for a question is `predictions` over its exclusion group.

## 7. Competing hypotheses
A Question node has a profile-defined boolean field `closed` (default false), set at creation. The group is informational: no operation retires a hypothesis because of group membership. A profile *may* declare that in a closed group the last non-retired member is auto-**proposed** (never auto-approved) for activation.

## 8. Profiles
A profile declares: node types with JSON Schemas for `fields` and an id prefix each; edge types (core ones may be given a `fields` schema; additional ones also declare allowed endpoint type pairs); gates (`{transition, node_type, roles}`); validators (callables `(project, write) -> None | RuleViolation`); an optional expansion hook; renderers; event hooks; agent guidance text. A project pins `name` and `version` in `.researchgraph/profile.yaml`. Two profiles ship with the core: `research` and `tasks` (`profiles.md`).
