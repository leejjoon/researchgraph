# Researchgraph: a small, general research-graph package for humans and agents

Oct 15, 2026 · @이재준

## Executive summary

Researchgraph is a small, domain-agnostic package that stores a research project as an append-only graph of typed nodes in a git repository, and lets both people and AI agents read it, act on it and extend it through the same operations. It is the foundation layer of astroharness, split out so that any field can use it and so that the astronomy-specific parts stay thin.

The package does four things and nothing else:

1. **Keeps the record.** Nodes (questions, hypotheses, tasks, results, claims, instructions, failures) and typed edges live as one file each in a git repository, with a disposable local index for speed. Nothing is edited in place; refinement creates a superseding node.
2. **Answers questions about the record.** What is ready to run, what is blocked, what supersedes what, which results a claim rests on, what changed since a date.
3. **Takes actions from humans and agents.** Propose, approve, supersede, attach an instruction, record a failure, claim work with a lease, complete. Every action is validated and attributed.
4. **Shows the graph.** An embeddable web component that renders the graph, opens a node, and exposes the actions above, with renderers for node artifacts (notebooks, files, text) supplied by plugins.

Domain content is a **profile**: a declared set of node types, edge types, gates and validators. The core ships with a generic research profile; astroharness supplies the astronomy one. The runner, formulas, provenance ledger and notebook execution stay in their own packages.

The closest existing projects are Atlas Graph, a hosted research graph, and hypotree, a local hypothesis DAG for agents. Researchgraph differs by keeping the record as open files in git, gating conclusions on human approval, and letting each field define its own profile; it plans exporters and adapters to both rather than competing on their ground.

Target: a usable v0.1 (Python library, CLI, MCP server, basic viewer) in four months, with astroharness as the first consumer and one non-astronomy pilot by v1.0.

## Why a separate package

The research graph is the part of astroharness that has nothing to do with astronomy, and the part most likely to be useful elsewhere. Splitting it out buys three things.

**A smaller, testable core.** The graph, its storage and its operations can be specified completely and tested without archives, pipelines or agents. Astroharness then becomes a profile plus formulas plus a runner, each small enough to review.

**Adoption beyond one field.** Biomedicine, chemistry and materials have domain agents and benchmarks but no researcher-owned hypothesis graph; the review of other fields in the astroharness proposal found none. A general package with a profile mechanism can be picked up by those communities without inheriting astronomy's tooling.

**A stable contract for agents.** Coding agents change every few months. If the graph's operations are a small, documented interface (CLI, Python, MCP), the agent side can be rewritten freely while projects, profiles and viewers stay put.

The cost is an extra package boundary to maintain and the temptation to over-generalise. The scope section guards against the second.

## Related work and differentiation

Two existing projects are close to researchgraph: Atlas Graph has nearly the same concept as a hosted service, and hypotree has many of the same mechanics as a local agent tool. Neither combines open, git-native storage with human approval gates and field-specific profiles, which is the position this package takes.

| Project | What it is | Shared with researchgraph | Where it differs |
| --- | --- | --- | --- |
| [Atlas Graph](https://apis.io/providers/synthetic-sciences/) (Synthetic Sciences) | Agent-first research graph platform with a CLI and REST API | Typed nodes for hypotheses, experiments, runs, evidence and decisions; humans and agents write directly; provenance per node; forking, subgraph export, roles | Hosted service requiring an account; the open-source workbench uses it but the graph itself is not open; no profile mechanism or approval gates described |
| [hypotree](https://pypi.org/project/hypotree/) | MIT-licensed hypothesis DAG with an MCP server and live dashboard | Leases on work, bi-temporal history, read-only dashboard, read-only agent mode, pre-registered benchmark | SQLite database outside git; the engine draws conclusions and prunes automatically rather than recording human-approved decisions; single maintainer, v0.6 |
| [Beads](https://github.com/gastownhall/beads) | Graph issue tracker for coding agents | Dependency graph, ready-work detection, claims, supersedes links | Dolt database; fixed schema for coding issues |
| [git-issues](https://pkg.go.dev/github.com/steviee/git-issues) | Git-native issue tracker for agents | One Markdown-plus-YAML file per item in the repository; dependency graph; agent skill | Coding tasks only; no research types, approvals or viewer |
| [scibraid](https://pypi.org/project/scibraid/), Constructor Hypothesis, SciAgents | Graphs built from the literature | Hypotheses and evidence as nodes, source-traceable | Graph of papers, not of the researcher's own project; complementary input |
| AiiDA, DataLad | Provenance of computations and data | Automatic records of what produced what | No hypotheses, claims or approvals; DataLad is a candidate large-file layer |

### Our position

- **Open and local where Atlas is hosted.** The record is the researcher's git repository, readable without the package or any account.
- **Files in git where hypotree and Beads use databases.** Graph, code and notebooks share one history and one review workflow.
- **Human-gated where hypotree is agent-autonomous.** Researchgraph records decisions people approve; it does not draw scientific conclusions itself.
- **Profile-extensible where all of them are fixed.** A field defines its own types, gates, validators and renderers.

### Interoperation rather than competition

- An **Atlas exporter** that writes a researchgraph project as an Atlas graph, so work can be published there without being locked in.
- **hypotree as an optional expansion engine**: a profile's expand hook can call hypotree to rank candidate tests, with researchgraph recording only what the researcher approves.
- **Literature graphs as inputs**: scibraid-style graphs attach as Literature nodes with their own provenance.
- **DataLad** as the default large-file layer, so its run records sit beside researchgraph's nodes.

### Borrowed from hypotree

Four ideas are adopted directly and described in the sections below: competing hypotheses as exclusion groups, "as of" queries over history, a read-only actor mode with operations marked as reads or writes, and a query for the weakest evidence under a claim.

## Scope and non-goals

Researchgraph owns the record and the operations on it. Everything that executes, judges or renders domain content lives in a consumer package.

### In scope

- The data model: node and edge types, statuses, lifecycle, provenance fields
- The project store layout in git and the local index
- Query and action operations, with validation and attribution
- Three interfaces over the same operations: Python API, CLI, MCP server
- The viewer: an embeddable web component and a minimal standalone app around it
- The profile mechanism and a generic research profile
- Export of the graph and its history for archiving and for training data
- An examples directory with a minimal reference runner script (poll ready, claim, run a shell command, complete or fail), unsupported, so the package can be tried end to end without a consumer package

### Non-goals

- Running anything: no runner, scheduler or queue beyond the claim-with-lease operation
- Domain procedures, formulas, likelihoods, pipelines
- Provenance of external data (archive IDs, calibration contexts): a profile may add fields, the core only stores them
- Notebook execution or rendering logic: the core calls renderers supplied by plugins
- Agent prompts or skills: the core publishes its operations; consumers write the guidance
- Multi-tenant hosting, accounts or permissions beyond git's

If a feature needs knowledge of a field, it belongs in a profile or a consumer package, not here.

### What the core is worth on its own

The bare core gives a researcher a durable, reviewable record of a project and a viewer to read and steer it. Most of the research benefit arrives with a profile and a runner on top; the proposal does not expect adoption of the bare core beyond trial use, and the `tasks` profile is a trial path, not a reason to adopt, since Beads and git-issues already cover plain task tracking more maturely.

## Data model

A project is a directed graph of typed nodes joined by typed edges, where nodes are never edited in meaning and every change is attributed to a person or an agent.

### Nodes

Every node has the same core fields; a profile adds typed extra fields and nothing else.

| Field | Meaning |
| --- | --- |
| id | Stable, content-independent identifier (short hash with a profile prefix) |
| type | One of the profile's node types |
| status | proposed, active, running, done, failed, retired. Blocked and ready are not stored; they are derived from blocks edges and approvals |
| title, body | Human-readable summary and Markdown detail |
| author | Person or agent identity, with kind (human or agent) and the agent's model and version when known |
| created, updated | Timestamps; updated changes only for status, approvals and attachments |
| approvals | List of approval records: who, when, what was approved |
| artifacts | References to files produced or attached, by path and hash |
| budget | Optional token and compute allowance for this node's subtree |
| fields | Profile-defined extras, validated against the profile's schema |

### The generic research profile

The core ships one profile so the package is usable on its own.

| Node type | Purpose |
| --- | --- |
| Question | What the project is trying to answer |
| Hypothesis | A candidate answer with its assumptions |
| Prediction | An observable or test that bears on a hypothesis |
| Task | Work to be done: gather, compute, analyse |
| Result | What a task produced, with artifacts |
| Claim | A statement supported by results |
| Instruction | Human guidance attached to a node before it runs |
| Failure | Why a node stopped: step, check, error, summary |
| Note | Free text, for anything else |

### Edges

| Edge | Meaning |
| --- | --- |
| blocks | Target cannot start until source is done |
| tests | A prediction or task tests a hypothesis |
| supersedes | Source replaces target; target is retired |
| derived-from | Source was produced from target |
| supports, contradicts | A claim bears on a hypothesis, with direction |
| discovered-from | Source was found while working on target |
| instructs | An instruction applies to a node |
| explains | A failure explains why a node failed |
| cites | A claim cites a result or artifact as evidence; the evidence, stale and weakest queries follow these edges |
| answers | A hypothesis answers a question; all hypotheses answering one question form its exclusion group |

Profiles may add edge types but cannot remove these.

### Append-only refinement

The only mutable things on a node are status, approvals, attachments, budget and the lease. To change what a node says, create a new node with a supersedes edge; the old one is retired, its edges are retired, and new edges are created from the replacement. Edges are never edited either. The graph therefore stays acyclic by construction, and every abandoned idea remains queryable.

### Lifecycle

proposed → active (on approval, where the profile requires one; immediately otherwise) → ready or blocked, derived from edges → running (claimed) → done or failed → retired (when superseded or rejected). Profiles declare which transitions need approval and by which role.

### Competing hypotheses

Hypotheses that answer the same question form an **exclusion group**, defined by their `answers` edges rather than by a list on the Question node, so adding a hypothesis never edits the question. The Question carries a flag, set at creation, saying whether the list is closed (these are all the possible answers) or open (others may exist). The group is information for people and tools, not an inference engine: when one member is supported by an approved claim, the viewer and the `alternatives` query show the rest as candidates for retirement, but nothing is retired without a human decision. For a closed group, a profile may allow the last surviving hypothesis to be proposed for activation automatically; for an open group it never is. This borrows hypotree's exclusion groups while keeping researchgraph's rule that conclusions are approved, not deduced.

## Storage

The record is files in a git repository; the index is a cache that can be deleted at any time.

### Layout

- `.researchgraph/profile.yaml`: which profile and version the project uses
- `graph/nodes/<id>.yaml` and `graph/edges/<id>.yaml`: one file each, so a change is a readable diff and a review
- `artifacts/<node id>/`: files attached to nodes; large files are handled by an optional data-versioning layer (DataLad, DVC or git LFS, chosen by the project) and referenced by hash
- `.researchgraph/index.sqlite`: disposable, gitignored

### Why files, not a database

A researcher can clone, fork, branch and archive the record with ordinary git, read it without the package installed, and review graph changes in a pull request. Collaboration needs no server: one node per file means concurrent work on different nodes never conflicts, and a conflict on the same node is a normal merge resolved by the people involved. Beads, which solved the same problem for coding agents, moved to a Dolt database for cell-level merge; we accept hand-resolved conflicts to keep one history for record, code and artifacts.

### The index

On open, the package scans the files and builds a SQLite index with tables for nodes, edges, status and a materialised view of readiness. Queries run against the index; writes go to files first, then update the index. A checksum of the file tree detects a stale index and rebuilds it. Projects of tens of thousands of nodes should open in seconds; the evaluation plan measures this.

For as-of queries the index also keeps a status-interval table: for every node, the commit range over which each status, approval and lease held. It is built once by walking git history and extended incrementally per new commit, so a scrubber moving through hundreds of instants reads intervals rather than checking out history. While agents are working, the index watches the project directory and the git log so running nodes, leases and failures appear within seconds of being written.

### Claims and leases

The one scheduling primitive in the core is claim-with-lease: an actor marks a ready node as running with an expiry. The claim is a small file committed like any other change, so it survives restarts and is visible to collaborators. A runner in another package decides what to do with claimed nodes.

## Interaction model

Humans and agents use the same operations through different doors, and every operation records who performed it and in what role.

| Door | Who uses it | Form |
| --- | --- | --- |
| Python API | Consumer packages, scripts, tests | `rg.open(path)`, then methods on the project object |
| CLI (`rg`) | Researchers in a terminal; agents via shell | One subcommand per operation, JSON output with `--json` |
| MCP server | Coding agents | One tool per operation, with the same names and arguments as the CLI |
| Web viewer | Researchers | Graph view, node inspector, action buttons calling the same operations |

### Identity and roles

Every call carries an actor: a human from git identity, or an agent from a declared name, model and version. Profiles define roles (for example owner, reviewer) and which approvals each role may give. The core enforces that an agent cannot approve anything a profile marks as human-only, whatever door it uses. The trust model is git's: identity comes from git config, which is adequate within one group and not for a shared public graph. A project may require signed commits for approvals, and the validator then refuses an approval whose commit signature does not match the approver.

### What agents may do

By default an agent may read everything, propose nodes and edges, claim and complete ready work, record results, failures and notes, and request approval. It may not approve, supersede an active hypothesis, change budgets, or retire nodes. A profile can loosen or tighten this.

### Read and write separation

Every operation is marked as a read or a write in the published interface, and the CLI, Python API and MCP server all expose that marking, so a host can gate writes without knowing the package. An actor can be opened **read-only**: it sees every query and is refused every write, including writes requested by name. This is the mode for reviewers, monitoring jobs and untrusted sub-agents. The MCP server also offers a small essential preset of operations for agents that already carry many tools, with the full set available on request. Claim counts as a write, because it issues a lease.

### What the human sees

The viewer presents a review queue of items needing a decision, the graph with history, and per-node actions. The CLI offers the same through `rg queue`, `rg show` and `rg approve`. Neither hides anything the other can do.

## Core operations

The interface is deliberately small: twelve actions and a query language, identical across the Python API, the CLI and the MCP server.

### Actions

| Operation | Effect | Default allowed to |
| --- | --- | --- |
| propose | Create a node in proposed status, with edges | Human, agent |
| approve | Move a node to active, recording who and why | Human (profile may delegate) |
| reject | Retire a proposed node with a reason | Human |
| supersede | Create a replacement node, retire the old one, re-point edges | Human; agent for task and result nodes only |
| instruct | Attach an Instruction node to a node that has not run | Human |
| claim | Mark a ready node running with a lease and actor | Agent, runner |
| release | Give up a claim without completing | Agent, runner |
| complete | Mark done, attach artifacts, record summary | Agent, runner |
| fail | Mark failed with a structured Failure node | Agent, runner |
| expand | Create an Expansion task under the selected nodes; a consumer's runner executes the profile's expansion hook and proposes children. The core never runs the hook itself | Human |
| budget | Set or change a subtree's budget | Human |
| export | Write the graph, history and selected artifacts as an archive or training-data bundle | Human |

### Queries

- `ready`: nodes whose blockers are done and whose approvals are complete
- `blocked`: nodes and what blocks them
- `history <node>`: the supersedes chain and all changes
- `evidence <claim>`: the results and artifacts a claim rests on, transitively
- `stale`: claims whose evidence has a newer superseding node
- `since <date>`: everything changed after a date
- `where <filter>`: by type, status, author, field value, budget use
- `--as-of <date or commit>`: any query evaluated against the graph as it stood at that moment, built from git history; the index caches status intervals so this stays fast
- `weakest <claim>`: the results under a claim ranked by how little supports them (single run, no held-out check, superseded inputs), as the cheapest places to test whether the claim would survive
- `alternatives <hypothesis>`: the other members of its exclusion group, their status and their evidence

### Validation

Every write passes the core's structural checks (acyclicity, allowed edge endpoints, required fields) and then the profile's validators. A rejected write returns the rule that failed. Validation is the mechanism that keeps guardrails in code rather than in agent prompts.

## Extensibility

A profile is a Python package that declares types, rules and hooks; the core discovers it by entry point and never needs to know the field.

| Extension point | What it supplies | Astroharness example |
| --- | --- | --- |
| Node and edge types | Names, extra fields with a schema, display labels | Sample, Dataset, Reduction, Analysis, Needs observation |
| Gates | Which transitions need approval and by which role | Activating a hypothesis; promoting a claim |
| Validators | Functions that accept or reject a write with a reason | A claim must cite artifacts with hashes |
| Expansion hook | Given selected nodes, return proposed children | The prediction protocol with its degeneracy matrix |
| Renderers | Turn a node's artifact into HTML for the viewer | Executed notebook with figures; FITS header summary |
| Event hooks | Run on status changes | Write ledger records; notify a runner |
| Agent guidance | Text the MCP server returns to agents on connect | How to follow a formula; what not to touch |

Profiles are versioned and a project pins one. Upgrading a profile runs its migration on the node files, which is itself a reviewable git change.

Two profiles ship with the core: the generic research profile, and a minimal `tasks` profile with only Task, Result and Note, so the package can be tried on an ordinary project in a minute.

## The graph viewer

The viewer is an embeddable web component, so astroharness and any other consumer can put the same graph into their own application instead of building one.

### What it is

A single JavaScript component (`<research-graph>`) plus a small standalone app that serves a project and hosts the component. The component speaks to the core through a local HTTP adapter over the same operations as the CLI, so there is one code path for every action.

### What it shows

- The graph, laid out by type and dependency, filterable by type, status, author and date, with supersedes chains collapsed into history by default
- A node inspector: fields, approvals, artifacts, failure record, instructions, and the rendered artifact via the profile's renderer
- A review queue: every pending approval with the context to decide
- Diff view between a node and the one it superseded, using the renderer's diff if it offers one (notebook diffs, for example) and a text diff otherwise
- A timeline scrubber that rewinds the whole graph to any past moment using the as-of query, so a group can see what it believed when a decision was made; retired and superseded nodes fade rather than disappear, because the point is that they were considered

### Live monitoring while agents work

The viewer doubles as a dashboard for a project under execution. It subscribes to the index's change events (server-sent events from the local adapter), so the graph updates without reloading as runners claim, complete and fail nodes.

- **Progress on the graph**: running nodes pulse, newly proposed nodes fade in, completed and failed nodes change state in place; a per-hypothesis progress bar shows done, running, ready, blocked and failed counts in its subtree
- **Activity panel**: who is working on what, which leases are active and when they expire, the queue of ready nodes nobody has claimed, and the review queue depth
- **Budget burn**: tokens and compute spent against each subtree's budget, with a warning as a branch approaches its limit
- **Failures feed**: new Failure nodes as they land, with the tripped check and a link to the node, so a researcher can intervene before dependent work queues up
- **Event log**: the ordered stream of status changes, filterable by actor, with the same as-of scrubber attached so the live view and the history view are one control
- **Summary digest**: what finished, what failed, what is waiting on a decision since the researcher last looked, shown on open and exportable as text

Everything on the dashboard is a read; it never writes evidence or changes state, and all numbers come from the index, so the dashboard is only as current as the files and git log it watches (seconds locally, the sync interval for a remote collaborator).

### What it lets people do

- Approve, reject, instruct, supersede and budget from the node; "stop" is budget set to zero, not a separate operation
- Re-run with instructions: one gesture that creates a superseding node inheriting the inputs and attaches an Instruction to it (supersede plus instruct)
- Select nodes and trigger expand, then activate or discard the proposals
- Pin an artifact to a hypothesis as its key evidence
- Mark an artifact or a part of one as evidence for a new claim, when the renderer supports selection

### What it does not do

It does not execute, edit artifacts or host accounts. Editing an artifact happens in the researcher's own tools; the result comes back as a superseding node. Access control is whatever protects the server and the git repository.

## How the packages fit

Researchgraph is the bottom layer; a profile adapts it to a field; a consumer such as astroharness adds execution, procedures and its own application on top.

&#91;embedded content: package layering: core, profiles, astroharness\]

Arrows mean "depends on". Astroharness packages depend on the astronomy profile and, through it, on the core: the runner uses the core's claim, complete and fail operations, the formulas supply renderers and validators to the profile, and the web app embeds the viewer component rather than building its own graph view. Coding agents enter through the core's MCP server whichever profile is loaded. Nothing in the core imports from a profile or a consumer.

## Evaluation

A storage and interaction library is judged on correctness, performance and whether people and agents can actually use it, not on scientific outcomes; those belong to the consumer packages.

| Measure | Target by v1.0 | How |
| --- | --- | --- |
| Invariant safety | Zero violations of acyclicity, append-only and permission rules under fuzzing | Property-based tests generating random operation sequences from both doors |
| Round trip | Export then import reproduces the graph byte for byte | Automated on every commit |
| Open time | A 20,000-node project opens in under 5 seconds on a laptop | Synthetic projects in CI |
| Merge behaviour | 100 concurrent branches editing distinct nodes merge without conflict | Scripted git scenarios |
| Agent usability | A coding agent completes a scripted ten-step session (propose, claim, complete, fail, request approval) through the MCP server without human help, on three agent products | Recorded sessions, repeated per release |
| Human usability | Five researchers outside the team complete inspect, re-run and expand tasks in the viewer in under 10 minutes each | Moderated sessions at v0.2 and v1.0 |
| Consumer fit | Astroharness profile implemented without forking the core; one non-astronomy profile built by an outside group | Reviewed at Gates 2 and 4 |
| Live update latency | A status change written by a runner appears in the viewer within 3 seconds locally, with 50 concurrent running nodes | Scripted runner load test in CI |

The tests are the specification: the data model and operations are documented by the property tests that enforce them.

## Roadmap

Four releases over twelve months, each closed by a gate that must pass before the next starts.

| Release | Months | Delivers | Gate |
| --- | --- | --- | --- |
| v0.1 | 1 to 4 | Data model with exclusion groups, file store, index with as-of queries, Python API, CLI with read-only mode, claim, release, complete and fail (needed by the astroharness runner in its month 3), generic and tasks profiles, property tests | Gate 1: invariants hold under fuzzing; astroharness profile drafted against the API without core changes |
| v0.2 | 5 to 7 | MCP server, export, change events from the index, viewer component with graph view, inspector, review queue and live monitoring (progress, activity, failures feed) | Gate 2: scripted agent session passes on two agent products; researcher usability session run |
| v0.3 | 8 to 10 | Expand, diff view, timeline scrubber unified with the event log, budget burn, summary digest, renderer plugin API, standalone app, profile migrations, Atlas exporter and hypotree adapter | Gate 3: astroharness runs a live project on the package; one outside group starts a second profile |
| v1.0 | 11 to 12 | Stable API, documentation, archive export format, second profile released | Gate 4: all evaluation targets met; API frozen for a release cycle |

Astroharness consumes each release as it lands, so the astronomy work proceeds in parallel rather than waiting.

## Team and effort

About 1.4 FTE in the first four months, tapering to about 0.6 FTE for the rest of the year as the astronomy-specific work takes the same people back.

| Role | Effort | Responsibilities |
| --- | --- | --- |
| Lead research software engineer | 0.8 FTE | Data model, store, index, operations, API, CLI, MCP server, tests |
| Frontend engineer | 0.5 FTE | Viewer component, standalone app, renderer plugin API |
| Product owner (researcher) | 0.1 FTE | Operation design, usability sessions, profile review |
| Second-profile partner | In-kind | Builds a non-astronomy profile from v0.3 |

The lead engineer and frontend engineer are the same people as in the astroharness plan, not additional hires. Months 1 to 4 go almost entirely to this package; from v0.2 they split roughly 40/60 between the graph package and astroharness. The totals in the table are the full-time-equivalents over the first four months.

## Risks and mitigations

The main risk of a general package is that it becomes general at astroharness's expense, either by over-abstracting or by slipping its schedule.

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Over-generalisation: features added for hypothetical fields | High | Medium | Every feature must be used by the astroharness profile or the second profile; otherwise it waits |
| Schedule slip delays astroharness | Medium | High | v0.1 scoped to what astroharness needs first; astroharness consumes releases as they land |
| File-per-node store too slow at scale | Low | Medium | Index measured in CI from v0.1; sharding of node directories if needed |
| Merge conflicts on hot nodes frustrate groups | Medium | Medium | If the open question on approvals-as-files is decided that way, status and approvals move to small sidecar files; conflict guidance in the viewer either way |
| Agents bypass the operations and edit files directly | Medium | High | Validators run on load, in an optional git pre-commit hook, and as a CI check on pull requests, since hooks are not installed on every clone; a project that fails validation refuses to open until repaired |
| Viewer becomes the whole project | Medium | Medium | Viewer limited to the listed interactions; renderers belong to profiles |
| No second adopter, package stays astronomy-only | Medium | Low | Tasks profile for trivial trial; partner identified before v0.3 |
| Atlas or hypotree becomes the de facto standard before v1.0 | Medium | High | Exporter to Atlas and hypotree adapter in v0.3; review at Gate 2 whether to contribute git storage to an existing project instead of continuing |

## Governance and licensing

- **Licence**: BSD-3-Clause, the same as astroharness, so the two can share code freely.
- **Repository**: its own repository and PyPI package, with astroharness depending on a pinned version.
- **API stability**: semantic versioning; the operations and file formats are the public API, the index is not.
- **Profiles**: the core maintainers review profile contributions for conformance only, not domain content.
- **Decisions**: a short design-notes directory records each decision with its alternatives, starting with the storage choice above.

## Open questions

- [ ] Name: is "researchgraph" available on PyPI and clear enough, or should it carry a less generic name?
- [ ] Should approvals be their own small files beside the node to reduce merge conflicts, at the cost of more files?
- [ ] Which field supplies the second profile, and who is the partner?
- [ ] Should the MCP server be part of this package or a separate thin package, so the core has no agent-protocol dependency?
- [ ] YAML or JSON for node files: readability versus strictness?
- [ ] Does the viewer ship as a web component only, or also as a JupyterLab panel later?
- [ ] Contact the Atlas and hypotree maintainers before v0.1 to test interest in a shared export format or a git storage backend?
- [ ] Should a closed exclusion group ever let the last surviving hypothesis be proposed automatically, or is that one step too close to drawing conclusions?

## References

- [Astroharness proposal](https://claude.ai/code/artifact/fd667ea2-1122-4dff-ba68-53e2972093f8), the first consumer of this package
- [Beads architecture and Dolt storage](https://beads.gascity.com/architecture), the prior art for an agent-facing task graph and the reason this package chose files in git
- [Beads, distributed graph issue tracker for AI agents](https://github.com/gastownhall/beads)
- [Xcientist: making AI scientists auditable from evidence to claim](https://arxiv.org/abs/2606.18874), source of the evidence-graph and claim-drift ideas
- [Karpathy, autoresearch](https://github.com/karpathy/autoresearch), the pattern behind claim, complete and fail
- [Synthetic Sciences, Atlas](https://apis.io/providers/synthetic-sciences/), and the open-source [OpenScience workbench](https://github.com/synthetic-sciences/openscience)
- [hypotree: persistent, self-revising hypothesis DAG for agentic R&D](https://pypi.org/project/hypotree/)
- [git-issues: git-native issue tracker](https://pkg.go.dev/github.com/steviee/git-issues)
- [scibraid: research-question graphs from the literature](https://pypi.org/project/scibraid/)
