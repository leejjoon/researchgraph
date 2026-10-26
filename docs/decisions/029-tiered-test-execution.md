# 029: Separate fast feedback from exhaustive verification

Status: accepted · 2026-10-26 · amends T17 CI scheduling, not graph semantics or release gates

## Decision

Routine main-branch pushes and pull requests run a bounded CPython core/CLI/CI
contract suite and static/manual-reference checks in `checks.yml`. Superseded
feedback runs may be cancelled. The same commands are documented for local work.
This is fast feedback, not full acceptance or release evidence.

The exhaustive `ci.yml` runs daily, on explicit workflow dispatch, and on `v*`
release-tag pushes. It retains all four operation doors, both cache modes, real
Chromium, installed-wheel/manual/notebook checks, CPython 3.11 and PyPy 3.11, and
all strict performance and monitoring gates. A release still requires successful
exhaustive evidence bound to the candidate commit; a passing fast workflow is
insufficient. `pytest tests` remains the full local suite by default.

Partition exhaustive CPython coverage into functional acceptance (`not
performance`) in both cache modes and a dedicated performance lane (`performance`)
in both modes. Do not repeat the separate installed-CLI pass: collection at
`a502d3b` showed all 462 dedicated CLI cases per cache mode already included among
the full suite's 3,110 cases for that mode. Do not repeat distribution, browser,
runner, documentation and notebook checks in the scale job when functional
acceptance already collects them. Compatibility checks remain separate because
they exercise different interpreters.

Run cache-independent functional cases only in the kept lane. The deleted lane
uses explicit `--index-fixtures-only` to select cases parametrized by
`index_mode`; default `--index-mode` behavior is unchanged. Collection regression
tests require the kept, deleted and performance lanes to be disjoint and their
union to equal the entire default full collection. Native cases with their own
cache checks still execute; they are not silently treated as deleted-fixture cases.

Upload runtime metadata, dependency disclosures, text logs and JUnit reports,
not entire temporary fixture trees. Tests deliberately create filenames that
GitHub's artifact uploader refuses, and these trees contain large disposable
repositories and environments. Failures, durations, deterministic seeds and
source identity remain in the retained reports. This does not remove any test or
convert failures into skips.

## Evidence and limits

On the development host, the bounded core selection passed 274 tests in 27.07 s;
one CLI query/cache-oracle case took 7.19 s overall. These are measurements of
specific selections, not a full-suite baseline or a promised CI duration.
After implementing the tiers, the documented fast commands passed 300 tests in
35.99 s and 32 documentation checks in 4.06 s. Three focused deleted-cache cases
also passed. Collection verification preserves all 3,817 full-suite node IDs
across the disjoint functional/cache and performance lanes; it is not a claim
that the exhaustive execution passed.

The existing hosted scrub/rebuild budget failures remain failures. This decision
does not relax the 2 s / 5 s budgets, reduce Hypothesis examples, replace real
transports with in-process mocks, or establish current release readiness.
