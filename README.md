# researchgraph

An append-only research graph in git, for humans and agents.

A project is a directory of small YAML files (nodes, edges) under version control, plus a disposable SQLite index. Nineteen operations (init, propose, link, attach, approve, reject, supersede, instruct, claim, renew, release, complete, fail, expand, budget, retire, tag, export, import) and a handful of queries are exposed identically through a Python API, a CLI (`rg`) and, from v0.2, an MCP server and an embeddable viewer. Domain content (node types, gates, validators, renderers) comes from a **profile**; the core knows no field.

- Start here if you are an agent: `AGENTS.md`
- Normative specification: `spec/`
- Work list: `tasks/v0.1.md`
- Why: `docs/proposal.md`, `docs/decisions/`
- Intended viewer: `docs/design/`

Licence: BSD-3-Clause.
