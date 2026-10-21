# researchgraph

An append-only research graph in git, for humans and agents.

A project is a directory of small YAML files (nodes, edges) under version control, plus a disposable SQLite index. Nineteen operations (init, propose, link, attach, approve, reject, supersede, instruct, claim, renew, release, complete, fail, expand, budget, retire, tag, export, import) and a handful of queries are exposed identically through a Python API, a CLI (`rg`) and, from v0.2, an MCP server and an embeddable viewer. Domain content (node types, gates, validators, renderers) comes from a **profile**; the core knows no field.

- Start here if you are an agent: `AGENTS.md`
- Executable three-task walkthrough: [quickstart](docs/quickstart.md)
- End-to-end workflows and complete CLI reference: [user manual](docs/user-manual.md)
- Python API and CLI integrations: [developer manual](docs/developer-manual.md)
- Synthetic research lifecycle: [tutorial notebook](docs/research-tutorial.ipynb), with [local setup and execution](docs/developer-manual.md#notebook-setup-and-execution)
- Normative specification: `spec/`
- Work list: `tasks/v0.1.md`
- Why: `docs/proposal.md`, `docs/decisions/`
- Intended viewer: `docs/design/`

## Using the API and console

Install from this checkout in an isolated environment; the quickstart includes the
complete commands. The two required native extensions build for the selected
Python interpreter/platform ABI, so a C compiler is needed. Python 3.11+ is
supported; no stable-ABI or free-threaded compatibility is claimed. The documented
PyPy 3.11 dependency constraint selects the tested `rpds-py==0.25.1` release of
jsonschema's existing dependency; it adds no core package dependency or output filter.

Use `.venv/bin/rg`, not an unqualified `rg`: the latter often names ripgrep.
Every operation/query has a command, with one JSON result on stdout under `--json`.
Domain errors are JSON on stderr, exit 2; absent/invalid projects exit 3.
Common options work before or after the command: `--project`, `--json`,
`--read-only`, `--as-agent NAME[:MODEL[:VERSION]]`, `--role`, and `--now`.
Writes also accept `--commit/--no-commit`; reads accept `--as-of`.
Commits require Git `user.name` and `user.email`; the quickstart configures only
the new demo repository, never your global identity.
Structured fields/usage are JSON; `--edge` and `--artifact` are repeatable.
Repeated `where --field K=V` filters conjoin; conflicting values for one key
are rejected, while identical repeats are allowed.

```python
from datetime import UTC, datetime
from researchgraph import Project
from researchgraph.model import Actor

with Project.open("demo") as project:
    ready = project.ready(actor=Actor("agent", "consumer"), now=datetime.now(UTC).replace(microsecond=0))
    print(ready["items"])
```

`Project`'s class and method docstrings document arguments, defaults, returned
records, effects, and errors (`help(Project)`). The complete operation and frozen
result-shape contracts are in [the normative operations spec](spec/operations.md).
Models and profile contracts are documented in `researchgraph.model` and
`researchgraph.profiles.base`. Use `Project.open`, `init`, or `import_` as context
managers to close index connections and helper processes deterministically.

## Operational boundaries

- **Declared identity is not authentication.** CLI humans come from Git's name
  and email; agent names/models/versions and roles are self-declared. Profile
  permission checks do not authorize filesystem access or authenticate a caller.
- **Files are authoritative.** `.researchgraph/index.sqlite` is disposable.
  Deleting it changes no graph meaning. Operations freshly check source paths,
  hash every current source byte, and resolve HEAD before using cached results.
- **No commit is still a real write.** `--no-commit` changes working-tree graph
  files and subsequent reads/index results, but not HEAD; it emits no event hook.
  Normal commits stage only that operation's files, leaving unrelated work alone.
  Validation errors leave source unchanged. A `write_failed` after commit or an
  event-hook failure can leave a durable commit: inspect files and HEAD.
- **Historical reads are partial.** `--as-of TIME|COMMIT` selects status, node/edge
  visibility, approvals and artifacts; commits mean committer timestamps.
  Budget, usage, tags, priority and lease remain current. It is not a checkout or
  full reconstruction of past mutable fields; equal-clock commits share a time.
- **Historical exports are not redaction.** Their as-of time selects membership,
  but selected graph/artifact files use current working-tree bytes and selected
  nodes retain complete status histories, including later events/intervals.
  Query an imported archive with `--as-of` to obtain the historical view.
  Approval, artifact and supersedes introduction provenance becomes the import
  commit; preserved status-interval commit IDs retain their recorded provenance.
- **Import clocks include future leases.** Import into an empty/nonexistent
  directory requires `now` at least every restored timestamp, including lease
  expiry and complete history. Equality is allowed; earlier times fail before
  destination creation. No timestamps are silently shifted.
- **The runner is an unsupported consumer, not core.**
  `examples/minimal_runner.py` accepts only the `tasks` profile, claims before
  executing `fields.command` in a shell as your OS user, and records success
  artifacts or a Failure with the exit code. It skips commandless Tasks and never
  retries terminal failures. Review commands and profiles before running them;
  there is no sandbox, scheduler, agent prompt, metering, or expansion-hook
  execution in the core. Viewer and MCP remain v0.2, not delivered v0.1 features.

## Verification

Install `.[test]` and `tests/testprofiles` into the isolated environment (use the
quickstart's existing-dependency constraint on PyPy 3.11), then run:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests examples setup.py
PYTHONHASHSEED=0 .venv/bin/python -m pytest tests --index-mode kept --hypothesis-seed=20261020 --basetemp=/tmp/researchgraph-kept
PYTHONHASHSEED=0 .venv/bin/python -m pytest tests --index-mode deleted --hypothesis-seed=20261020 --basetemp=/tmp/researchgraph-deleted
.venv/bin/python -m pytest tests -m cli --index-mode both
```

`--index-mode` selects the actual acceptance fixture (`both` by default), not a
global cache hook; independent unit and scale tests are unchanged. Temporary
projects must live outside the checkout so they cannot inherit its Git repository.
CI defines full and dedicated installed-console gates on CPython 3.14.4, plus
focused CPython/PyPy 3.11 functionality checks, not interpreter-wide performance
claims. Scale reports retain the unchanged 5,000-Task/200-public-historical-call
<2s gate and a fresh 20,000-Task rebuild <5s excluding only the separately measured
Git walk; full public-call time is also reported. JUnit properties and temporary
fixtures are uploaded even on failure. Local execution is separate evidence from
a hosted Actions run; neither proves the separate external astroharness Gate 1.

Licence: BSD-3-Clause.
