# 014: Bounded native source discovery and template cloning

Status: accepted implementation decision

## Decision
Use two required, private C extensions for fixed computations: fresh POSIX graph/history path discovery and canonical assembly of current file-hash replies, and compilation/cloning of structurally proven flat query templates. Build them with the existing setuptools backend and public Python C API. No new Python library dependency, persistent format, public operation, scheduler, or source-of-truth change is introduced.

The extensions are required, not optional accelerators. Import/build failures remain visible; there is no Python fallback or hidden retry. Source builds require a C11 compiler, Python development headers and POSIX directory APIs. Build binaries for the actual interpreter/version/platform ABI; do not claim a limited/stable ABI. The Python >=3.11 requirement and existing POSIX functional support remain. Native use does not justify silently declaring CPython-only support. Verify the available CPython 3.11, CPython 3.14 and PyPy 3.11 installations; do not infer free-threaded or subinterpreter compatibility from a conventional CPython benchmark.

## Boundaries
- Every public operation still freshly discovers and checks source paths, hashes every graph/history byte, and resolves fresh HEAD. Discovery streams only currently discovered regular files to the existing bounded hash helpers; canonical sorted framing and complete-scan publication remain unchanged.
- A framing descriptor retains only immutable path/header/offset metadata, with zero-filled hash slots. Every call must supply exactly one current SHA32 for every canonical path before a complete framing buffer can be hashed. Reject missing, duplicate, unknown or malformed replies. Retain no source digest/reply across operations; assemble current raw batches without per-file Python digest objects. Finish all source checks and byte hashing before resolving fresh HEAD.
- Once all current byte hashes have completed, the fixed HEAD process may overlap independent, non-I/O canonical frame assembly. Await and validate that fresh HEAD before publishing a response; assembly errors close the spawned process and propagate. Do not move HEAD acquisition before complete source discovery or byte hashing.
- Native path errors cross one narrow `PathError` to `RuleViolation("schema", ...)` bridge. Ordinary I/O, allocation and internal batch-sink failures propagate unchanged. Failed discovery cannot publish partial path/framing caches; the existing owner tears down outstanding hash work.
- Compile only the existing fixed-schema, flat eligibility class. Private temporary holders give ordinary dictionaries shared immutable key storage. Arbitrary profile-field mappings remain ordinary mappings; no public wrapper or alternative DTO schema is introduced.
- The compiled descriptor privately owns copies of scalar-only template dictionaries and immutable string tags. Its acyclic ownership proof excludes nested containers, custom objects and owner references. Release it with its source epoch; do not accumulate historical descriptors.
- Every clone eagerly allocates complete ordinary dictionaries/lists, including separate author, fields, approvals, artifacts and tags containers for every row and call. Preserve canonical key order, actor metadata, NaN, signed zero and numeric types. Complex/dynamic nodes retain the existing semantic serializer, not a compatibility fallback for the same native path.
- Do not alter GC policy/tracking, freshness, output cardinality, the two-second threshold, or the whole-loop timer.

## Why
The unchanged product workload—5,000 Tasks, 200 distinct actual commits, 200 public historical `ready` calls returning all 4,999/5,000 applicable rows—still took 2.367816 seconds on default CPython 3.14.4 after bounded source-scan overlap and pipeline improvements. Correctness-focused integration passed 243 tests. Interpreter-level enumeration, template copying and allocation remain measured costs.

Temporary combined native prototypes completed five unchanged whole public sweeps in 1.847887, 1.652486, 1.715374, 1.846608 and 1.918570 seconds on the same host/default CPython 3.14.4. These are feasibility diagnostics, not installed-product acceptance. Earlier failed cohorts, including a 2.062890-second first repetition of the previous prototype, remain part of the evidence.

The integrated implementation subsequently passed 835 release-reference tests, but a fresh consecutive performance cohort failed its third repetition: 1.895642, 1.911619, then 2.282389 seconds. This remains a failed cohort. Native canonical assembly removes the remaining per-file Python digest slicing/map/framing allocations; it does not change the workload, timing boundary or required source work.

With canonical assembly and post-byte-hash HEAD overlap, the pre-audit default CPython 3.14.4 suite passed 895 tests and five consecutive whole public sweeps completed in 1.684946, 1.627576, 1.634609, 1.529390 and 1.519467 seconds. Independent review then identified failed-HEAD classification, idle helper digest ownership, unexpected SQLite probe-error masking and closed-Index history retention. Nine regressions failed before their fixes.

After those repairs, the release-reference suite passes 904 tests. Five new consecutive unchanged whole public sweeps complete in 1.697772, 1.695479, 1.820103, 1.705279 and 1.639218 seconds. The fresh 20,000-node rebuild takes 2.529231 seconds excluding only the git-history walk. Rebuilt source/wheel contents and the isolated installed CLI/API lifecycle are verified. The 406-test native/source/query/history cohort passes on both CPython 3.11 and PyPy 3.11. These functional checks do not assert the same wall-clock budget on every interpreter; earlier interpreter-specific full-suite failures remain distinct from release-reference acceptance.

## Delivery gates
Tests precede integration: native ownership/error/cleanup properties, canonical equality and independent public mutation; source discovery and byte-freshness regressions; installed source-distribution/wheel and CLI smoke checks; functional verification on the retained runtimes; the full release-reference suite on disclosed default CPython 3.14.4; and five consecutive unchanged whole public performance gates, all below two seconds. No best-of statistic, skipped failure, private-snapshot substitution, runtime GC knob or timer adjustment establishes acceptance. Recheck the existing 20,000-node rebuild budget excluding only the specified git-history walk.

Use the existing task-prefixed T12 commit boundary only after genuine acceptance. T13–T18 remain ordered and required.
