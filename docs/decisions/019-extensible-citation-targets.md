# 019: Profiles may add explicit citation targets

Status: accepted · 2026-10-21

## Decision

Permit a profile to redeclare `cites.allowed` as a superset of the core pairs
`(Claim, Result)` and `(Claim, Reference)`, adding only explicit target names
with Claim as the source. Preserve both core pairs even when those types are
undeclared; such pairs cannot match a stored endpoint. Reject wildcard pairs,
narrowing, other sources and changes to other core edge endpoints with the
existing profile-load `schema` error. Edge schemas and inheritance retain
their existing extension rules.

`evidence`, `stale` and `weakest` require only the reserved Claim vocabulary,
not Result or Reference. Traverse all permitted targets identically without
renaming stored immutable types. `weakest` orders direct non-retired citation
targets by `(has no superseded input, runs, held_out, id)`. Superseded input
includes the target itself and its non-retired cites/derived-from closure.
For any permitted declared target, supplied `runs` must be a non-boolean
integer and `held_out` a boolean; missing values score as 0 and false. Validate
on write with `schema`; do not coerce values. The profile schema may impose
additional restrictions such as nonnegative runs. Query defaults do not
materialise new stored fields or override profile defaults.

## Rationale and alternatives

Profile-defined literature or measurement nodes are evidence without being
Results or References. Renaming existing nodes violates append-only storage;
consumer-side scoring would diverge across operation doors. Arbitrary endpoint
rewrites would weaken core invariants. A narrowly additive exception preserves
all existing edges and gives the queries one vocabulary-independent path.

## Consequences

Rewrite the reserved-vocabulary fallbacks and weakest contract together.
Claim/Literature-only fixtures must prove traversal, staleness and ranking;
paired target fixtures compare order under an identifier correspondence, not
byte equality. Existing Reference records and other core edge rules remain
unchanged. No migration or new stored representation is introduced.
