# Profiles (normative)

A profile is a Python package exposing an entry point in group `researchgraph.profiles` whose value is a `Profile` object. `Profile.register(profile)` adds one at runtime for the current process (used by API tests for ad-hoc profiles); `Profile.load(name)` resolves registered profiles before entry points. The test suite also ships an installable `researchgraph-testprofiles` package exposing the ad-hoc profiles through entry points, so the CLI (subprocess) half of the suite can load them.

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
    transition: str = "activate"   # the only value in v0.1; others raise unsupported_gate_transition on load
    node_type: str = "*"           # or a node type name
    roles: list[str] = field(default_factory=lambda: ["human"])   # roles that satisfy it; "human" means any human
    agents_allowed: bool = False

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
    event_hooks: list[Callable] = field(default_factory=list)     # called after a committed write
    agent_guidance: str = ""
    migrations: dict[str, Callable] = field(default_factory=dict) # from-version -> fn(project)
```

A type requires approval iff some gate's `node_type` is its name or `"*"`; when several gates match, each must be satisfied. A gate is satisfied by one approval whose actor fits `roles`: a human fits when its declared role is in `roles` or `roles` contains `"human"`; an agent fits only when `agents_allowed` and its declared role is in `roles` or `roles` contains `"agent"`. An agent approving a gate without `agents_allowed` gets `human_only_approval`; any other misfit gets `wrong_role`.

A project's `.researchgraph/profile.yaml` pins `name` and `version`. Opening a project whose pinned version is newer than the installed profile fails with `profile_too_new`; an older pinned version runs the migration chain in v0.3 and in v0.1 opens unchanged.

All schemas are closed: the core sets `additionalProperties: false` on every node and edge schema, overriding any value the profile gave. Schema `default` values are materialised into `fields` at write time.

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
- A profile cannot remove core edge types or core invariants, and cannot change a core edge type's endpoints; it may add a `fields` schema or change `inherit`.
- The names in `data-model.md` §8 are reserved; a profile that omits one gets the stated fallback behaviour.
