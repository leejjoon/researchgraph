# AGENTS.md — how to work in this repository

You are building **researchgraph**: a small Python package that stores a research project as an append-only graph of typed nodes in a git repository, with the same operations exposed to people (CLI, viewer) and agents (CLI, MCP).

## Read in this order

1. `spec/` — **normative**. If code and spec disagree, the spec wins; if the spec is wrong, write a decision record first (see below), then change the spec, then the code.
2. `tasks/v0.2.md` — the current ordered work list; work top to bottom. `tasks/v0.1.md` is the retained baseline, not the current queue. Each item defines "done".
3. `spec/acceptance/` — Given/When/Then scenarios, one file per operation. Turn each into tests before implementing the operation.
4. `docs/proposal.md` — background and rationale. Read once; do not re-derive decisions it already argues.
5. `docs/decisions/` — why things are the way they are. Do not relitigate without a new record.
6. `docs/design/` — the viewer's intended look (three `.dc.html` screens), subject to the implemented v0.2 scope and deferred controls.

## Rules

- **Tests first.** Every operation in `spec/operations.md` gets property-based tests (hypothesis) for the invariants in `spec/data-model.md` before implementation. The invariants are: acyclicity, append-only, permission rules, file/index consistency.
- **Never edit `spec/` to make a test pass.** Add `docs/decisions/NNN-<slug>.md` explaining the change, then update the spec in the same commit.
- **Files are the source of truth.** The SQLite index is a cache; any code path that reads from the index must behave identically after `rm .researchgraph/index.sqlite`.
- **No runner, no scheduler, no agent prompts in the core.** The only scheduling primitive is `claim` with a lease. If you find yourself writing a loop that decides what to run next, stop; it belongs in `examples/` or a consumer package.
- **No field knowledge in the core.** Anything that mentions astronomy, biology, or any discipline belongs in a profile.
- **One commit per task item**, small and reviewable. Commit message starts with the task id (`T07:`).
- **Python 3.11+**, `pyproject.toml` with `uv` or `pip`. Core dependencies: `pyyaml`, `click`, `jsonschema`, `zstandard`; use declared test tooling. Add dependencies only with a decision record (see 011, 016, 017).
- **v0.2 interfaces are optional adapters.** The `mcp` extra supplies the official SDK/stdio server; `viewer` supplies Starlette/Uvicorn HTTP/SSE and the packaged framework-free viewer. Keep optional imports out of core-only API/CLI paths. Preserve all 37 graph operations across API, CLI `--json`, MCP and HTTP; `events`, `mcp` and `serve` are separate stream/server commands. Enforce launch identity, roles, read-only and loopback/session security at the adapters, not client convention.
- **Required browser proof uses real Chromium.** Install `.[test,mcp,viewer]` and run `.venv/bin/python -m playwright install --with-deps chromium` for designated CPython browser gates. Missing required tooling fails, never silently skips; fake DOM checks do not replace browser acceptance or monitoring latency evidence.
- Ask (leave a question in `tasks/questions.md`) rather than guess when the spec is silent on something that affects the file format. File format mistakes are expensive to migrate.

## Retained v0.1 definition of done

`pytest` passes; `rg init`, `rg propose`, `rg link`, `rg approve`, `rg ready`, `rg claim`, `rg complete`, `rg fail`, `rg history`, `rg evidence`, `rg export`, `rg import` work end to end on a fresh repository; `examples/minimal_runner.py` runs a three-node project to completion; `docs/quickstart.md` reproduces that in under 30 lines.

## v0.2 completion boundary

Retain every v0.1 gate and satisfy `tasks/v0.2.md`'s four-door, both-cache-mode, browser and scale gates. External Gate 2 evidence remains incomplete: deterministic replay, local browser checks and documentation do not establish real unassisted agent-product recordings, an outside-researcher usability session or external profile adoption. Do not claim v0.2 complete without those recorded outcomes.
