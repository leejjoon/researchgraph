# Documentation requirements — agreed interview results

## Status

This document exports the requirements agreed during seven interview rounds.
It is a readable requirements record, not an implementation plan, a formally
approved workflow specification, or authorization to implement the deliverables.
The user authorized this export separately; the saved interview state predates
that authorization and still records `export_authorized: false`.

The interview's final recorded ambiguity was **3.2%**, below its **5%** threshold.
No unresolved requirements disputes were recorded. Formal interview finalization
remains pending a GJC tooling limitation: the question tool could not supply the
metadata needed to record the workflow's canonical scope confirmation. The answers
and summary are preserved; this limitation does not indicate lost requirements
or a defect in researchgraph. The ambiguity score describes the original
interview, not this revision.

### Revision

Revised 2026-10-21 after a two-member committee review. The seven agreed
decisions are unchanged and the table below is verbatim. This revision adds the
constraints an implementer would otherwise have to guess, each taken from the
repository as it stands: the profile and actors the notebook uses and how human
approval happens in an automated run; where artifacts live and what "saved
result" means; what the synthetic experiment may depend on; that executing a
notebook needs tooling the project does not have today; how "complete reference"
is checked; how the manuals relate to `README.md`, `docs/quickstart.md` and the
tests that already cover them; what the documentation must not promise; and the
determinism and isolation rules that make examples verifiable. It also records
that the cited interview state file is local and untracked. Nine sentences beyond the interview record were marked for maintainer
decision; they are now decided. On 2026-10-21 the maintainer accepted the committee's
recommendations on all nine such items; the decisions are written into the text
below and no markers remain. Decision record 016 covers the notebook tooling.

## Objective

Enable people to learn and test the current researchgraph implementation without
having to reconstruct everyday usage from its normative specifications. Deliver
self-contained user and external-integration documentation, supported by one
executable tutorial using synthetic data.

The documentation describes the implementation at the commit it is verified
against, and states that package version (`researchgraph.__version__`, 0.0.1
today), the v0.1 specification milestone, and the bundled profile versions. It
is descriptive, not normative. `spec/` remains the contract; a disagreement
between a manual and the specification is a documentation bug or a
specification/implementation mismatch to report; `spec/` is never changed
solely to make a documentation check pass, and a specification correction
requires a decision record first.

## Deliverables

### 1. User manual

- A self-contained Markdown manual in the repository, linked from `README.md`.
- Explain prerequisites, concepts, detailed end-to-end workflows, and
  troubleshooting without requiring readers to study the specifications.
- Include a complete, concise reference for every CLI operation and query.
- Detailed workflow examples are required; a separate full worked example for
  every command is not required.
- Prerequisites cover the supported Python versions, Git and a C compiler,
  isolated installation from a checkout, project-local Git identity, and how to
  invoke the installed console without colliding with ripgrep's `rg`.
- Concepts cover nodes, directed edges, profiles, actor kinds and declared
  roles, activation gates, task leases, artifacts, evidence, and
  supersession/history, as a user meets them. Describe the two bundled profiles:
  which types are gated (`research`: Hypothesis, Prediction and Claim, by any
  human) and which are claimable (Task only); the `owner` and `reviewer` roles;
  and that the `tasks` profile has no gates. Describing the bundled profiles is
  not profile authoring.
- The reference covers all 35 `rg` subcommands (18 writes, `export` as a read,
  and 16 queries), using `spec/operations.md` as the normative inventory and the
  installed `rg --help` to verify syntax; each subcommand's options; the common
  options (`--project`, `--json`, `--read-only`, `--as-agent`, `--role`,
  `--now`, `--commit/--no-commit`, and `--as-of` on reads); and the exit codes
  (0; 2 for domain errors, reported as JSON on stderr, and for CLI usage
  errors, reported as plain text; 3 for a missing or invalid project). `init`, `link`, `attach` and `import`
  are included. Each entry gives invocation, arguments and options with
  defaults, permissions or preconditions, effect or result shape, and the errors
  it can raise; shared conventions may be documented once.
- Troubleshooting covers at least the shared rules every write can raise
  (`read_only`, `clock_skew`, `not_found`, `profile_too_new`,
  `human_only_approval`, `wrong_role`, `unsupported_by_profile`,
  `write_failed`), the Git `user.name`/`user.email` requirement, lease expiry
  and reclaim, and that deleting `.researchgraph/index.sqlite` is safe.
- Restates the operational boundaries already in `README.md` (declared identity
  is not authentication; files are authoritative; `--no-commit` is a real
  write; historical reads and exports are partial) rather than contradicting
  them.
- `docs/quickstart.md` is kept as it is (its length and content are enforced by
  `tests/test_docs.py` and the v0.1 definition of done) and is cross-linked from
  the manual, not absorbed; the manual explains how the quickstart (`tasks`
  profile, reference runner) and the research tutorial differ.
- Workflow examples presented as runnable in the user manual are exercised by
  the smoke tests of acceptance criterion 6; fragments that are not runnable
  are marked as such.

### 2. Developer manual for external integrators

- A self-contained Markdown manual in the repository, linked from `README.md`.
- Target developers building an external integration using the Python API or CLI.
- Explain the integration-relevant concepts, setup, examples, and troubleshooting
  without making specification reading a prerequisite.
- Include CLI/API examples that can be checked by automated smoke tests.
- Profile authoring and contributing to researchgraph's core are not the selected
  audiences for this manual; an exhaustive catalogue of internal Python helpers
  is not required.
- Covers the public Python surface: `researchgraph.Project` opened through
  `open`, `init` or `import_` as a context manager (or closed explicitly); every
  operation and query method with the `actor`, `role`, `now`, `commit` and
  `as_of` keyword conventions, including that the API's `now` is explicit while
  the CLI defaults to the wall clock; `researchgraph.Actor`,
  `researchgraph.RuleViolation` and `researchgraph.__version__`; the
  `researchgraph.model` records that appear in arguments and results; and the
  CLI/API naming differences (`import` versus `import_`). `get_node`,
  `get_nodes` and `get_edges` are documented as public low-level read methods:
  they return current `researchgraph.model` records, accept no `actor`, `now`
  or `as_of`, and include retired records. `Project.apply` is outside the
  integration API; the manual mentions it once as the low-level `Write`
  boundary with no CLI or MCP door, describing the implementation rather than
  a frozen contract, and directs integrators to the named operations.
- Covers the CLI contract that wrappers depend on: structured JSON arguments and
  repeatable options, the `--json` result shapes frozen at T15, exit codes and
  the JSON error form on stderr, `--read-only`, `--as-of` and its documented
  limitations, `--now` and `clock_skew`, `--as-agent`/`--role`,
  `--commit/--no-commit` and when event hooks fire, import destination and
  clock requirements, and export from a read-only project.
- Installation for integrators is from a source checkout or a locally built
  wheel; no package-index publication is promised.
- Runnable examples are deterministic where they assert on output: fixed
  `now`/`--now` values that advance between writes, explicitly declared actors,
  demo repositories with their own Git identity, use of returned identifiers
  rather than assumed fixed ids, commit hashes or wall-clock values, and no
  network access.
- States what integrators cannot rely on in v0.1: the viewer, the MCP server,
  profile renderers, profile migrations, execution of expansion hooks, the
  reference runner (unsupported), authentication, and coordination of
  concurrent writes across clones.

### 3. One executable research tutorial notebook

- One required Jupyter notebook providing a complete end-to-end tutorial with
  synthetic data, rather than an optional notebook or multiple tutorials.
- Run locally from a fresh checkout using documented isolated-environment setup,
  including installation of the selected kernel and execution tooling.
- Demonstrate this research lifecycle:

  **Question → hypothesis/prediction → synthetic experiment → saved result →
  evidence-backed, human-approved claim.**

- Use the bundled `research` profile, pinned by `init`, as recorded in round 5,
  rather than substituting the simpler, ungated task-chain example for the
  chosen outcome. The `tasks` profile and `examples/minimal_runner.py` are not
  used for the lifecycle; the runner refuses the research profile, so the
  notebook claims and completes its Task through the API or CLI itself. The
  notebook is a tutorial, not a runner: one explicit claim/complete cycle, no
  loop that decides what to run next.
- Make the saved results and final evidence-backed, human-approved claim
  observable through executable checks.
- Minimum resulting graph: a Question; a Hypothesis with `answers` to it; a
  Prediction with `tests` to the Hypothesis carrying `expected`; a Task with
  `addresses` to the Prediction, claimed and completed; a Result with
  `derived-from` to the Task; a Claim with `cites` to the Result, linked before
  the Claim is approved, and approved by the human actor; and a `supports` or
  `contradicts` edge from the Claim to the Hypothesis. Both the Hypothesis and
  the Prediction are required.
- Actors: at least one declared human actor performs the three gated approvals
  (Hypothesis, Prediction, Claim). The notebook names its human review points
  and what to inspect before each approval, and explains that the research
  profile's gates admit any human, that its roles do not impose a reviewer-only
  gate, and that agents cannot satisfy them. In an automated run the three
  gated approvals are performed by the same declared human actor; the notebook
  and its test identify these as simulated approval, demonstrating the
  declared-identity contract without asserting that a person reviewed the
  automated run. The experiment's Task is claimed and completed by one declared
  agent actor, distinct from the human approver, so its `claim` and `complete`
  records show the human/agent split.
- "Saved result" means the Result node holding at least one artifact record
  whose file lies under `artifacts/<result id>/`, registered through `attach`,
  which records an existing file and does not create or move it. Output the
  Task itself produced under `artifacts/<task id>/` may additionally be
  registered through `complete`.
- "Evidence-backed, human-approved" means the research profile's
  `claim_needs_evidence` validator is satisfied (a non-retired `cites` edge
  exists before approval) and the Claim's approval record carries a human
  actor. The validator checks that the relation exists, not the scientific
  validity of the conclusion; an approved Claim ends in `active`, not `done`.
- Synthetic experiment: data generated inside the notebook with a fixed seed,
  using only the Python standard library and researchgraph; no network access,
  no external dataset, no new runtime dependency, nothing added to the core.
- Observable checks: assertion cells inside the notebook verify, through the
  public API or `--json` output, the node statuses, the approval records' actor
  kinds, the `cites` edge, that `evidence <claim>` lists the Result with its
  artifacts, and that artifact files hash to their records. Checks use the
  identifiers the operations return and never assume fixed generated ids,
  commit hashes or wall-clock values.
- Isolation: the notebook creates its demo project at `<temporary parent>/demo`,
  where the parent is a fresh `tempfile.mkdtemp(prefix="researchgraph-tutorial-")`
  directory verified to be outside the checkout. It prints the absolute project
  path, refuses an existing demo directory, and retains the project for
  inspection and external verification. Automated runs set the kernel's
  `TMPDIR` to a location under pytest's temporary directory outside the
  checkout and locate the single parent there. The notebook configures Git
  identity only in the demo repository, never touches global Git
  configuration, and runs headless (no `input()`, no widgets). The documented
  setup makes the isolated environment's interpreter the notebook's kernel.
- Tooling dependency: the test suite executes the actual notebook with
  `nbclient`, reads it with `nbformat`, and runs it on `ipykernel` from the
  isolated environment. These three packages are declared in the existing
  `test` extra, with versions that install across the CI interpreter matrix;
  none becomes a core runtime dependency. Their addition is authorized by
  decision record 016.
- Stored form: the notebook is committed with code-cell outputs empty,
  execution counts null, and execution-specific metadata removed, retaining
  explanatory Markdown and portable kernel metadata; a test enforces this.
  Verification executes the notebook afresh and checks graph and artifact
  semantics, not output bytes.

## Acceptance criteria

1. Both manuals are self-contained Markdown documents, discoverable through
   working `README.md` links and usable without first reading the normative
   specifications; the notebook and its setup and execution instructions are
   discoverable through the documentation. Changes to `README.md` are limited
   to navigation; its single executed Python block and `docs/quickstart.md`
   remain as the existing tests require.
2. The user manual combines detailed workflows with a complete concise reference
   covering every CLI operation and query in the current implementation: all 35
   subcommands, their options, the common options, and the exit codes, with the
   entry structure given under Deliverable 1.
3. The developer manual addresses external Python API/CLI integrations, with
   executable examples appropriate to that audience, and covers the public API
   surface and CLI contract listed under Deliverable 2, including permissions,
   errors, returned data, and resource handling.
4. The single notebook runs from a fresh local checkout with documented isolated
   setup and demonstrates the agreed synthetic research lifecycle on the
   `research` profile through saved results and an evidence-backed,
   human-approved claim, with the approvals made by a declared human actor under
   the simulated-approval interpretation above.
5. Automated notebook execution verifies its resulting graph and artifacts: the
   test suite executes the actual notebook sequentially from a clean kernel in a
   temporary location outside the checkout, with the isolation
   `tests/test_docs.py` already uses (no `PYTHONPATH`, no global Git
   configuration), without relying on saved outputs or prepared state; it fails
   on any execution error or unmet assertion; and it then checks the resulting
   project from outside the notebook (statuses, approval actor kinds, edges,
   artifact records and file hashes, evidence reachable from the Claim), not
   merely that every cell ran.
6. Automated smoke tests exercise the CLI/API examples by extracting them from
   the published Markdown (or from a shared source the Markdown is generated
   from), so the tested text is the text readers see; every example marked
   runnable in either manual is covered; CLI examples run the installed console
   executable, and the tests assert the documented results or state changes.
7. Automated reference-coverage checks verify completeness of the CLI operation
   and query reference against both the installed CLI (`rg --help` and each
   `rg <command> --help`) and `spec/operations.md`, and fail when a command or
   option lacks an entry, when the reference names something that does not
   exist, when an entry lacks the required structure, or when the command
   inventories of `rg --help` and `spec/operations.md` disagree with each
   other. Semantic accuracy is covered by the
   smoke tests and review, not inferred from entry presence. A check that the
   developer manual mentions every public `Project` method is not required.
8. Verification controls synthetic inputs and clocks: explicit whole-second UTC
   `now`/`--now` values, advanced between writes where history matters, with
   assertions on returned identifiers rather than golden ids, paths or commit
   hashes.
9. The checks behind criteria 5 to 7 run in CI on the existing CPython gates,
   with a documented local command and no silent skip when required tooling is
   missing; `.github/workflows/ci.yml` and the job lists pinned by
   `tests/test_ci.py` are updated accordingly. Notebook execution lives in
   `tests/test_notebook.py` and runs in the CPython 3.14.4 acceptance and scale
   jobs. The CPython 3.11 and PyPy compatibility job keeps running
   `tests/test_docs.py` and does not select `tests/test_notebook.py`;
   `tests/test_ci.py` enforces these selections, and jobs that execute the
   notebook never silently skip for missing tooling.

## Scope boundaries

- Markdown manuals plus the notebook satisfy the agreed meaning of standalone.
- An HTML documentation site and PDF generation are not required.
- Google Colab or other hosted-notebook support is not required.
- Profile-authoring and core-contributor manuals are outside the selected scope;
  describing the two bundled profiles is inside it.
- The notebook need not duplicate the entire command reference.
- The manuals describe v0.1 as implemented and do not promise the viewer, the MCP
  server, profile renderers, profile migrations, expansion-hook execution, a
  published package, authentication, command sandboxing, or coordination of
  concurrent writes across clones; they preserve the existing boundaries
  against a core runner, scheduler, agent prompts, or discipline-specific core
  behaviour.
- The documentation covers the Python API and the CLI only; the `--json` shapes
  it documents are those frozen at T15.
- This export does not authorize implementation, dependency changes, or changes
  to researchgraph's behavior or normative contracts. Adding `nbclient`,
  `nbformat` and `ipykernel` to the `test` extra is the one anticipated
  exception, authorized by decision record 016.

## Decisions by interview round

| Round | Agreed decision |
| --- | --- |
| 1 | Require one complete end-to-end tutorial with synthetic data. |
| 2 | Target the developer manual at external integrations using the Python API or CLI. |
| 3 | Provide detailed user workflows plus a complete concise reference for every CLI operation and query. |
| 4 | Support local execution from a fresh checkout with documented isolated-environment setup. |
| 5 | Teach the research lifecycle ending in saved results and an evidence-backed, human-approved claim. |
| 6 | Deliver self-contained Markdown manuals plus the notebook; no HTML/PDF publishing requirement. |
| 7 | Require automated notebook execution, CLI/API example smoke tests, and complete-reference coverage checks. |

## Implementation choices not prescribed by the interview

The exact synthetic experiment; the notebook and manual filenames and
directories (subject to the `README.md` linking and test constraints above); the
versions pinned for the notebook tooling; and the mechanism that extracts
Markdown examples for smoke tests and checks reference coverage are downstream
design choices. They must preserve the confirmed scope and acceptance criteria
rather than silently introducing another product or weakening the verification
requirements. The demo project's location, the notebook test file and its CI
job placement are decided above and are not open.

## Source record

The seven answered rounds and requirements summary are preserved in:

`.gjc/_session-01a15521-b964-7387-aad9-9d27f8a5cefb/state/deep-interview-state.json`

That directory is excluded from the repository (`.git/info/exclude`) and from
built distributions (`tests/test_packaging.py`); it is untracked here and
absent from any checkout or distribution, and it does not record the later
export authorization or this revision. For every other reader this document is the complete
requirements record, and the table above is the durable copy of the seven
decisions.

The interview was retained pending recorder repair at the user's request.
This readable export does not replace the saved answers or claim formal workflow
approval. Contract clarifications in this revision are grounded in `AGENTS.md`,
`spec/operations.md`, `spec/profiles.md`, `spec/data-model.md`,
`spec/store-layout.md`, the acceptance scenarios, and the existing installed
interfaces and documentation tests.
