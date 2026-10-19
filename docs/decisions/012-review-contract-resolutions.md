# 012: Deterministic archives, import chronology, stale causes, and schema defaults

Status: accepted · 2026-10-20

## Decision
1. Export uses POSIX PAX rather than USTAR. Keep sorted members, fixed metadata and zstd level 3. Use Python `tarfile.PAX_FORMAT` with UTF-8 strict encoding and initially empty per-member PAX headers, without global headers; canonical header encoding is specified in `spec/store-layout.md`. This supports long and Unicode artifact paths without adding attachment restrictions.
2. Before creating destination files, import rejects `now` earlier than any restored graph timestamp or non-null history interval timestamp with `clock_skew`. Equality is allowed. Preserve archived timestamps; do not clamp or rewrite them. The import commit then supplies a valid HEAD clock floor for subsequent writes.
3. `stale` returns each claim once with `responsible`: an array of distinct replacement-head IDs sorted lexicographically, including every superseded input in its evidence closure. Even a single cause is a one-element array.
4. Only direct properties of the root fields schema may declare defaults. Profile loading rejects defaults at any other schema location with `unsupported_schema_default`; a null direct-property default or one that fails its property schema raises `invalid_schema_default`. Null defaults are not inserted because fields with null values are omitted. At write time, drop explicit null fields, deep-copy defaults for absent direct properties, then validate the complete fields mapping. Composition remains available for validation, not default selection.
5. `explains` edges are created by `fail` and may be inherited by `supersede`; direct creation through `link` or `propose` remains forbidden.

## Why
The readiness review found four places where valid input could not satisfy the promised output or admitted incompatible implementations: artifact names beyond USTAR limits, imported history newer than the local clock, multiple independent stale causes, and conflicting defaults in composed schemas. These are contract fixes, not an architectural redesign. The `explains` wording omitted inheritance already required by decision 006.

## Alternatives
- Restrict artifact paths to USTAR limits: rejects otherwise valid project files for a legacy archive limitation.
- Rewrite timestamps on import: changes the research record and breaks historical query equivalence.
- Select one stale cause: hides other independently superseded evidence.
- Implement branch-dependent default selection: unnecessary complexity for v0.1; a narrow explicit default policy is sufficient.

## Consequences
- Normative contracts and acceptance scenarios change together; T04, T08, T10 and T14 include the new cases.
- Decision 011's default-materialisation rule is narrowed by this decision. No dependency is added: PAX is provided by Python's standard library.
- This change updates the specification only; executable operation tests and implementation remain on the ordered v0.1 task list.
