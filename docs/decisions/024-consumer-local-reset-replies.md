# 024 — Consumer-local reset replies

Accepted: 2026-10-21

## Context

The live viewer has independent graph/dashboard subscriptions to one bounded
ChangeSource. With retention one, an expired consumer currently appends a new
index reset to the shared window. That evicts the other consumer's current
cursor; its recovery appends another reset, evicting the first. Both can then
reset forever without any persisted, HEAD or clock change. Real Chromium's
retention-one reconnect acceptance exposed this as an indefinitely refreshing
snapshot, not a network timeout to relax.

## Decision

A cursor-recovery reply is local to the requesting consumer. Return one detached
copy of the current retained index with `reset: true`; do not increment the source
sequence, append to its window or invalidate another consumer. Its cursor and
`at` identify that retained associated observation, not the recovery request's
wall time. The current index is always the final envelope of an observation.

Actual state disappearance still emits a shared index reset with a new sequence.
Actual persisted/HEAD/clock changes still emit deltas/index normally. A consumer
receiving reset must refetch; replay and snapshot cursors retain their existing
format. No storage, authorization, dependency or durable replay change occurs.

## Consequences

Multiple slow or reconnecting consumers converge even at minimum retention.
Recovery no longer fabricates source observations. Tests cover independently
current/expired/malformed consumers, detached reset containers and repeated
unchanged reads, in addition to real browser reconnect and disappearance cases.
