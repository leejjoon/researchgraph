# 020: Bounded observed-state notifications with restart-safe cursors

Status: accepted · 2026-10-21

## Decision

Graph reads return all nodes and edges (including retired), sorted by created
then id with existing as-of limitations. Profile reads expose pinned identity
and currently loaded declaration data, even under as-of; never callables.
Their installed command rows land with implementation, not this contract.

Derive notifications by polling persisted state and evaluation time, not
profile hooks, operation success or cache rebuilds. Include external and
no-commit writes, HEAD-only changes and clock-only lease transitions. An
already refreshed index must not hide changes from an observer. Validation
and fully rolled-back failures cause no domain change; commit failure with
retained bytes or post-commit hook failure is observed normally. Consumers
converge on files, not an assumed rollback or a retried mutation.

Each ChangeSource owns a random 32-lowercase-hex generation independent of
the source fingerprint/content_hash. Sequence numbers increase per envelope.
All doors use the complete opaque cursor `v1:<generation>:<seq>`; no truncated
SSE ids. A retained cursor resumes only its successors. Malformed, unknown,
expired or previous-instance cursors reset with one index envelope. Disappeared
records also reset, because the delta vocabulary cannot express deletion.
Restart always invalidates old cursors, even with unchanged bytes.

Use bounded in-memory retention, coalesce changes between observations, and
reset lagging consumers rather than accumulate unbounded buffers. A snapshot
must be subscribed with its cursor (or followed by a cursor-aware catch-up);
on reset refetch state. Observation time and observed HEAD are not causal
timestamps or provenance. No persistent event file or durable replay promise.
Deleting the cache preserves state equality, not cursor/generation/time identity.

## Rationale and alternatives

Hooks miss independent processes, no-commit changes and failed retained writes.
File watchers add dependencies without establishing the state contract.
Durable event sourcing would introduce a second source of truth. Polling the
existing source fingerprint and clock plus state diff is smaller and converges
on the canonical files. A content hash is not a generation: unchanged bytes
after restart must not accidentally accept an old overlapping sequence.

## Consequences

Fold tests compare an initial graph plus deltas/reset snapshots with fresh
reads. Fault, temporal, external-process, cache, retention and restart tests
are required. CLI streams and SSE carry identical envelopes/cursors. Slow
consumers may lose intermediate observations but can always recover state;
events are not a complete historical audit trail. Historical/provenance reads
remain the responsibility of history and the existing as-of contract.
