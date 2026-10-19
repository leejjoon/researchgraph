# 011: Canonical YAML form and the dependency set

Status: accepted · 2026-10-19

## Decision
- The on-disk format is YAML as emitted by PyYAML (a YAML 1.1 library) with a fixed set of options, specified as a "Canonical form" in `store-layout.md`. Strings that YAML 1.1 would read as anything but a string are double-quoted; multi-line strings use the literal block style; no line wrapping; timestamps are whole seconds.
- Top-level node and edge keys are always written, in the order of the data-model tables, with explicit `null`, `[]` or `{}` when empty. Inside `fields`, optional keys with no value are omitted and schema defaults are materialised at write time.
- Dependencies: `pyyaml`, `click`, `jsonschema` (fields validation), `zstandard` (export archives). Test extras: `pytest`, `hypothesis`, `pytest-benchmark`. Lint: `ruff`.

## Why
AGENTS.md fixes `pyyaml`; a "YAML 1.2" promise cannot be kept with it, and a canonical serialiser with no diff on rewrite needs every formatting choice fixed. `jsonschema` and `pytest-benchmark` were already in `pyproject.toml` without a record. Export needs a zstd library.

## Alternatives
- ruamel.yaml for YAML 1.2: adds a heavier dependency for no change in what projects store.
