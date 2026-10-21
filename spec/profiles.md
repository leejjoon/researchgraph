# Profiles (normative)

A profile is a Python package exposing an entry point in group `researchgraph.profiles` whose value is a `Profile` object. `Profile.register(profile)` adds one at runtime for the current process (used by API tests for ad-hoc profiles); `Profile.load(name)` resolves registered profiles before entry points. The test suite also ships an installable `researchgraph-testprofiles` package under `tests/testprofiles/` exposing the ad-hoc profiles through entry points, so the CLI (subprocess) half of the suite can load them.

```python
@dataclass
class NodeType:
    name: str                 # "Hypothesis"
    prefix: str               # "hy"; 2–4 lowercase letters, unique within the profile
    schema: dict              # JSON Schema for node.fields; the core adds additionalProperties: false
    claimable: bool = False   # listed by `ready` and accepted by `claim`

@dataclass
class EdgeType:
    name: str
    allowed: list[tuple[str, str]]            # (from type, to type); "*" wildcard
    schema: dict = field(default_factory=dict) # JSON Schema for edge.fields; {} = no fields allowed
    inherit: str = "outgoing"                 # "both" | "outgoing" | "none" (data-model.md §3)

@dataclass
class Gate:
    transition: str = "activate"   # the only value in v0.2; others raise unsupported_gate_transition on load
    node_type: str = "*"           # or a node type name
    roles: list[str] = field(default_factory=lambda: ["human"])   # roles that satisfy it; "human" means any human
    agents_allowed: bool = False
    when_fields: dict | None = None # exact scalar conjunction on immutable materialised fields

@dataclass
class Write:                       # what validators see
    op: str                        # operation name
    actor: Actor
    role: str | None
    now: datetime
    args: dict                     # the operation's arguments
    nodes: list[Node]              # nodes as they will be after the write (new and changed)
    edges: list[Edge]              # edges as they will be after the write

@dataclass
class Profile:
    name: str
    version: str
    node_types: list[NodeType]
    edge_types: list[EdgeType]   # core types may be re-declared to add a schema or inherit; others are added
    gates: list[Gate]
    roles: list[str]
    validators: list[Callable[["Project", Write], None]]   # raise RuleViolation
    expansion_hook: Callable | None = None   # NOT called by the core; documented for consumers
    renderers: dict[str, Callable] = field(default_factory=dict)  # v0.2
    event_hooks: list[Callable[["Project", Write, str], None]] = field(default_factory=list)  # (project, write, commit id) after a committed write
    agent_guidance: str = ""
    migrations: dict[str, Callable] = field(default_factory=dict) # from-version -> fn(project)
```

A node requires approval iff at least one activate gate matches its type and immutable materialised `fields` (decision 018). A gate's type matches its declared `node_type` or `"*"`. Absent `when_fields` is unconditional; otherwise every condition key must be present and its scalar value must match exactly, without coercion. Strings, booleans and numbers retain their JSON types: `true` does not match `1`, nor does `"1"` match `1`. An empty mapping is an unconditional conjunction. Match after dropping null fields and materialising defaults, before choosing the initial status. Missing keys never match.

At profile load, non-mapping conditions, null/array/object condition values, unknown direct `properties` keys, and any `when_fields` mapping on `node_type="*"` raise `schema`. Conditions do not evaluate expressions or access nested keys. Every constructor evaluates its own fields, including generated Failure, Task and Instruction nodes and the replacement and optional Instruction made by `supersede`. Supersession re-evaluates the replacement's conditions and inherits no approvals. Node and approval record formats are unchanged.

When several gates match, each must be satisfied. A gate is satisfied by one approval whose actor fits `roles`: a human fits when its declared role is in `roles` or `roles` contains `"human"`; an agent fits only when `agents_allowed` and (its declared role is in `roles` or `roles` contains `"agent"`). Preserve the existing permission boundary: if any matching gate disallows agents, an agent approval gets `human_only_approval`; any other misfit gets `wrong_role`. One approval may satisfy several matching gates; approvals for some but not all matching gates leave the node proposed. Unmatched gates have no effect on approval permissions or queue entries. A proposed claimable node cannot be claimed before all matching activation gates are satisfied.

A project's `.researchgraph/profile.yaml` pins `name` and `version`. Versions are dotted integers compared as tuples (`1.10` > `1.9`). Opening a project whose pinned version is newer than the installed profile fails with `profile_too_new`; an older pinned version runs the migration chain in v0.3 and in v0.1 opens unchanged.

All schemas are closed: the core sets `additionalProperties: false` on every node and edge schema, overriding any value the profile gave.

### Defaults (decision 012)
- Only a direct property schema at root `properties.<name>` may declare `default`. Defaults at the root itself, in nested property or array-item schemas, in definitions, or within composition/conditional subschemas are rejected when the profile is loaded with `RuleViolation(rule="unsupported_schema_default")`. This applies equally to registered profiles and entry points, and to node and edge schemas. Inspect schema locations, not arbitrary data: a key named `default` inside a literal default value is not a schema keyword.
- Each supported default must be non-null and validate against its property schema; otherwise profile loading raises `RuleViolation(rule="invalid_schema_default")`. Null is not a stored fields value, so a null default is rejected rather than inserted and then removed. Check unsupported default locations before validating default values.
- At write time, first drop explicit null entries in the supplied root `fields` mapping. For each absent direct property with a default, insert a deep copy of that value; never replace a supplied non-null value or mutate the profile schema. Then validate the complete mapping, including required properties and composition constraints; failure raises `schema` before any files are written.
- Composition remains supported for validation; it does not select defaults or cause recursive materialisation. An object or array default is copied as a whole, without synthesising defaults for its children.

## `research` profile (ships with core)

Node types (`fields` schema as JSON Schema properties; `*` = required):

| Type | Prefix | Claimable | Fields |
| --- | --- | --- | --- |
| Question | `qu` | no | `closed: boolean, default false` |
| Reference | `rf` | no | `doi: string`, `url: string`, `title: string` (field-specific identifiers such as a bibcode belong in a downstream profile) |
| Hypothesis | `hy` | no | none |
| Prediction | `pr` | no | none |
| Task | `ta` | yes | `kind: string`, `targets: array of string`, `command: string` |
| Result | `re` | no | `held_out: boolean`, `runs: integer, minimum 0` |
| Claim | `cl` | no | none |
| Instruction | `in` | no | none |
| Failure | `fl` | no | `step: string`, `check: string`, `measured: string`, `error: string` |
| Note | `no` | no | none |

Roles: `owner`, `reviewer`.
Gates: activate Hypothesis, Prediction and Claim by any human (`roles: ["human"]`). No other gates.
Edge schemas: `tests` → `{expected*: string, discriminating: boolean default true}`; `supports`/`contradicts` → `{strength: enum weak|moderate|strong}`; `cites` → `{artifact: string, locator: string, sha256: string}`; `pins` → `{artifact*: string}`; all others `{}`.
Endpoint rules: as the core table, plus `cites` Claim → Result or Reference.
Validators: `claim_needs_evidence` (a Claim must have ≥ 1 non-retired `cites` edge before `approve`; raises `RuleViolation(rule="claim_needs_evidence")`); `pin_artifact_exists` (a `pins` edge's `fields.artifact` must be a path in the target Result's `artifacts`; raises `schema`); `non_discriminating` is a warning computed by the core (`data-model.md` §6), not a refusal.

## `tasks` profile (ships with core)

| Type | Prefix | Claimable | Fields |
| --- | --- | --- | --- |
| Task | `ta` | yes | `kind: string`, `targets: array of string`, `command: string` |
| Result | `re` | no | none |
| Instruction | `in` | no | none |
| Failure | `fl` | no | `step: string`, `check: string`, `measured: string`, `error: string` |
| Note | `no` | no | none |

No gates, no roles, no validators. For trials and for `examples/minimal_runner.py`, which runs `fields.command` of ready Tasks and ignores Tasks without one.

## Rules for the core
- The core imports no profile by name except to register the two bundled ones.
- The core calls validators on every write, event hooks after every committed write, and never calls `expansion_hook`.
- A profile cannot remove core edge types or core invariants. It may add a `fields` schema or change `inherit`. Core endpoints cannot change except for the additive `cites` extension below (decision 019).
- A redeclared `cites.allowed` must retain both `(Claim, Result)` and `(Claim, Reference)` and may add explicit target types with Claim as the only source. Wildcards, narrowing, other sources and endpoint changes to any other core edge raise `schema` at profile load. Pairs naming undeclared types remain present but never match an endpoint. No existing stored node type is renamed.
- `evidence`, `stale` and `weakest` require only Claim and treat every permitted citation target identically, including in profiles with neither Result nor Reference. On any declared permitted target, supplied `fields.runs` must be an integer (not a boolean), and supplied `fields.held_out` a boolean; otherwise writes raise `schema`. Profile schemas may restrict them further. Missing scoring fields mean 0 and false in `weakest`, without storing synthetic fields or coercing supplied values.
- The names in `data-model.md` §8 are reserved; a profile that omits one gets the stated fallback behaviour.
