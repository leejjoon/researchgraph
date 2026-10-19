# 010: Lifecycle clarifications: leases, supersede, reject, budget, usage, actors

Status: accepted · 2026-10-19

## Decision
- **Expired leases.** `lease_expired(n)` is `n.status == running and now >= n.lease.expires_at`. `ready(n)` is true for a running node whose lease has expired; the stored status stays `running` until the next write. `claim` on such a node replaces the holder atomically (`running → running`). Until then the recorded holder may still `renew`, `complete`, `fail` or `release`. `renew` sets `expires_at = max(current, now + lease_seconds)` and never shortens a lease. Every query takes a `now` parameter so tests fix the clock.
- **Supersede from running** is refused with `RuleViolation(rule="running")`, mirroring `retire`. Allowed sources are `proposed`, `active`, `done`, `failed`.
- **Reject retires edges**, as `retire` does. Otherwise a rejected blocker blocks forever and a rejected hypothesis stays in its exclusion group.
- **Budget** comparison is inclusive: `over_budget` when any dimension's summed usage is `>=` the limit, so a zero budget stops work immediately.
- **Usage** is additive: each `complete`, `fail` or `release` adds the supplied amounts to the node's stored totals. `release` gains `usage?`. Retiring or superseding a descendant does not remove its usage from ancestor totals.
- **Actors.** The stored actor everywhere (`author`, `lease.actor`, `approvals[].actor`, `artifacts[].added_by`) is the mapping `{kind, name, email?, model?, version?}`. Identity is `(kind, name)`. Roles are self-declared per call (`--role` on the CLI); the trust model is declared identity, as the proposal says. Commit author is the human's git identity, or `NAME <NAME@agents.researchgraph>` for agents.
- **Every write appends one record to `approvals`** when it changes status or records a human decision. `what` is one of `approve, reject, retire, claim, renew, release, complete, fail, superseded, budget`; `note` carries the operation's summary, reason or note.

## Why
The texts on lease expiry disagreed four ways. Allowing supersede on a running node would void another actor's lease. The other items were undefined and each admitted two implementations with different observable behaviour.

## Consequences
- The lifecycle table and several acceptance scenarios are amended.
- `approvals` is the per-node event log that `history` reads; it keeps the node file self-describing without a sidecar.
