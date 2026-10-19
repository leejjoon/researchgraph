# AGENTS.md — how to work in this repository

You are building **researchgraph**: a small Python package that stores a research project as an append-only graph of typed nodes in a git repository, with the same operations exposed to people (CLI, viewer) and agents (CLI, MCP).

## Read in this order

1. `spec/` — **normative**. If code and spec disagree, the spec wins; if the spec is wrong, write a decision record first (see below), then change the spec, then the code.
2. `tasks/v0.1.md` — the ordered work list. Work it top to bottom. Each item says what "done" means.
3. `spec/acceptance/` — Given/When/Then scenarios, one file per operation. Turn each into tests before implementing the operation.
4. `docs/proposal.md` — background and rationale. Read once; do not re-derive decisions it already argues.
5. `docs/decisions/` — why things are the way they are. Do not relitigate without a new record.
6. `docs/design/` — the viewer's intended look (three `.dc.html` screens). Reference for v0.2+, not v0.1.

## Rules

- **Tests first.** Every operation in `spec/operations.md` gets property-based tests (hypothesis) for the invariants in `spec/data-model.md` before implementation. The invariants are: acyclicity, append-only, permission rules, file/index consistency.
- **Never edit `spec/` to make a test pass.** Add `docs/decisions/NNN-<slug>.md` explaining the change, then update the spec in the same commit.
- **Files are the source of truth.** The SQLite index is a cache; any code path that reads from the index must behave identically after `rm .researchgraph/index.sqlite`.
- **No runner, no scheduler, no agent prompts in the core.** The only scheduling primitive is `claim` with a lease. If you find yourself writing a loop that decides what to run next, stop; it belongs in `examples/` or a consumer package.
- **No field knowledge in the core.** Anything that mentions astronomy, biology, or any discipline belongs in a profile.
- **One commit per task item**, small and reviewable. Commit message starts with the task id (`T07:`).
- **Python 3.11+**, `pyproject.toml` with `uv` or `pip`. Dependencies: `pyyaml`, `click`, `jsonschema`, `zstandard`; `hypothesis`, `pytest-benchmark`, `ruff` (tests). Add others only with a decision record (see 011).
- **Do not build the viewer or MCP server in v0.1.** They are v0.2. The v0.1 CLI must expose every operation with `--json` output so the viewer and MCP can be thin wrappers later.
- Ask (leave a question in `tasks/questions.md`) rather than guess when the spec is silent on something that affects the file format. File format mistakes are expensive to migrate.

## Definition of done for the whole v0.1

`pytest` passes; `rg init`, `rg propose`, `rg link`, `rg approve`, `rg ready`, `rg claim`, `rg complete`, `rg fail`, `rg history`, `rg evidence`, `rg export`, `rg import` work end to end on a fresh repository; `examples/minimal_runner.py` runs a three-node project to completion; `docs/quickstart.md` reproduces that in under 30 lines.
