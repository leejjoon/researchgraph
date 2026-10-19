# 002: The core does not run anything

Status: accepted · 2026-10-15

## Decision
researchgraph provides exactly one scheduling primitive: `claim` with a lease, plus `release`, `complete` and `fail`. It never starts a process, never chooses what to run next, never calls a profile's expansion hook. `expand` only creates an Expansion task node.

## Why
Running agents couples the core to an agent product, a compute backend and a field. Consumers (astroharness) own that. Keeping it out makes the core testable without any agent and keeps "no field knowledge in the core" true.

## Consequences
- `examples/minimal_runner.py` exists so the package can be tried end to end, and is explicitly unsupported.
- The viewer's "re-run" and "expand" buttons create nodes; a runner picks them up.
