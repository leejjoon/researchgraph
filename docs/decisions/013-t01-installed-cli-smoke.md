# 013: T01 verifies the installed CLI with a real smoke test

Status: accepted · 2026-10-20

## Decision
T01 includes a real subprocess test of the installed researchgraph `rg --version` command, checking its identity, output and successful exit. Pytest must collect and pass that test. The test remains in the suite as a packaging regression check.

## Why
The previous criterion, "pytest runs zero tests green", conflicted with pytest's normal no-tests-collected exit code 5. During consensus planning the maintainer explicitly selected an observable installed-console test and passing pytest instead. Empty-suite exit masking and fake assertions are not acceptable substitutes.

## Consequences
- Amend T01's done-when before implementation; the real smoke test belongs in the T01 implementation commit.
- Resolve the installed environment's console script explicitly because the host command `rg` may be ripgrep.
- No runtime dependency or product file format changes.
