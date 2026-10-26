# 028: Name the installed console command rgraph

Status: accepted · 2026-10-26 · amends CLI naming in operations and acceptance scenarios

## Decision

The researchgraph distribution installs `rgraph`, not `rg`. There is no `rg`
console alias. CLI operations, options and exit codes are unchanged. The Python
package, profile entry-point group, MCP server identity and `.researchgraph/`
store directory retain their existing names.

Current command examples, launch configurations, installed-console tests and CI
use `rgraph`. Documentation examples use `RGRAPH` for the absolute console path.
The internal `rg-example` documentation markers are not command names and remain
unchanged.

## Why

`rg` is already the widely used ripgrep command. Requiring users to distinguish
two unrelated executables by absolute path is unnecessary and error-prone. The
slightly longer `rgraph` is identifiable without that collision.

## Provenance boundary

The stored Git commit-message prefix remains `rg:`. In particular, `rg: import`
is used when reconstructing imported history; changing it would change the
storage/history contract, not just the executable name. No graph, archive or
index format changes are required.

Existing decision records, retained task baselines, design mockups and recorded
evaluation sessions keep their historical command spellings. They describe older
source revisions, not current launch instructions. This decision supersedes their
CLI naming where they are used as current requirements.

## Verification

Installed-console tests check `rgraph --version`, help, and operation behavior.
Distribution metadata and fresh-wheel installation tests require `rgraph` as the
sole researchgraph console entry point. They do not require removing another
package's `rg` executable, which may legitimately be ripgrep.
