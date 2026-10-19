# Profiles (normative)

A profile is a Python package exposing an entry point in group `researchgraph.profiles` whose value is a `Profile` object.

```python
@dataclass
class NodeType:
    name: str           # "Hypothesis"
    prefix: str         # "hy"
    schema: dict        # JSON Schema for node.fields
    requires_gate: bool # proposed→active needs approve

@dataclass
class EdgeType:
    name: str
    allowed: list[tuple[str, str]]   # (from type, to type); "*" wildcard
    schema: dict = field(default_factory=dict)   # JSON Schema for edge.fields; {} = no fields allowed

@dataclass
class Gate:
    transition: str     # "activate" | "run" | "complete"
    node_type: str      # or "*"
    roles: list[str]    # roles that may satisfy it; "human" means any human
    agents_allowed: bool = False

@dataclass
class Profile:
    name: str
    version: str
    node_types: list[NodeType]
    edge_types: list[EdgeType]   # added to the core set
    gates: list[Gate]
    roles: list[str]
    validators: list[Callable[["Project", "Write"], None]]   # raise RuleViolation
    expansion_hook: Callable | None = None   # NOT called by the core; documented for consumers
    renderers: dict[str, Callable] = field(default_factory=dict)  # v0.2
    event_hooks: list[Callable] = field(default_factory=list)     # called after a committed write
    agent_guidance: str = ""
    migrations: dict[str, Callable] = field(default_factory=dict) # from-version -> fn(project)
```

A project's `.researchgraph/profile.yaml` pins `name` and `version`. Opening a project whose pinned version is newer than the installed profile fails with `profile_too_new`; older runs the migration chain (v0.3).

## `research` profile (ships with core)
Node types: Question (`qu`, fields: `closed: bool=false`), Reference (`rf`, fields: `doi?: str, url?: str, bibcode?: str, title?: str`), Hypothesis (`hy`, requires gate), Prediction (`pr`, requires gate), Task (`ta`, fields: `kind?: str`, `targets?: [id]`), Result (`re`, fields: `held_out?: bool`, `runs?: int`), Claim (`cl`, requires gate), Instruction (`in`), Failure (`fl`, fields: `step?, check?, measured?, error?`), Note (`no`).
Roles: `owner`, `reviewer`.
Gates: activate Hypothesis/Prediction/Claim by any human; run Task by none (no gate).
Edge schemas: `tests` → `{expected: str (required), discriminating: bool (default true)}`; `supports`/`contradicts` → `{strength?: weak|moderate|strong}`; `cites` → `{artifact?: str, locator?: str, sha256?: str}`; `pins` → `{artifact: str (required)}`; all others `{}`.
Endpoint rules: `addresses` Task → Prediction; `about` Note → any; `pins` Hypothesis → Result; `cites` Claim → Result or Reference.
Validators: a Claim must have ≥1 `cites` edge before `approve`; `answers` only from Hypothesis to Question; `tests` only to Hypothesis; a `tests` edge whose `expected` equals that of every other `tests` edge from the same Prediction is flagged `non_discriminating` (warning in `show`, not a refusal).

## `tasks` profile (ships with core)
Node types: Task (`ta`), Result (`re`), Note (`no`). No gates. For trials only.

## Rules for the core
- The core imports no profile by name except to register the two bundled ones.
- The core calls validators on every write, event hooks after every committed write, and never calls `expansion_hook`.
- A profile cannot remove core edge types or core invariants.
