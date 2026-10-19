# 009: Only activation gates in v0.1; node types declare whether they are claimable

Status: accepted · 2026-10-19

## Decision
1. A `Gate` may only have `transition: activate` in v0.1. Loading a profile that declares `run` or `complete` gates raises `unsupported_gate_transition`. `NodeType.requires_gate` is removed; a type requires approval when some activate gate matches it. `approve.what` is `activate` and nothing else. Every node-creating operation (`propose`, `supersede`, `expand`, `instruct`, `fail`) creates its node `proposed` when the profile gates that type, else `active`.
2. `NodeType` gains `claimable: bool = False`. `ready(n)` requires `n.type` to be claimable; `claim` on a non-claimable node raises `not_ready`. `blocked` stays type-agnostic. In the bundled profiles only `Task` is claimable.

## Why
`approve` raises `not_proposed` on an active node, so a run gate could never be satisfied, and a complete gate had no defined effect. Neither bundled profile needs them and every gate the astroharness note lists is an activation gate.

Without a type condition, `ready` listed Instructions, Failures and Notes, and a runner that claims what `ready` returns would claim a Failure, find no command, fail it, create another Failure, and loop. A flag in the profile makes `ready`, `digest` and `claim` mean "work" for every consumer, including later MCP agents, instead of every consumer repeating the same filter.

## Alternatives
- Keep `ready` type-agnostic and add `--type`: pushes the filter to every consumer and leaves the loop possible.

## Consequences
- `profiles.md` dataclasses change; `expand.md` 1 and `propose.md` 2 remain valid.
