# 016: Notebook execution tooling lives in the `test` extra

Status: accepted · 2026-10-21 · authorises the dependency exception anticipated by `docs/documentation-requirements.md`

## Decision
1. `nbclient`, `nbformat` and `ipykernel` are added to the `test` extra in `pyproject.toml`, pinned to versions that install on every interpreter in the CI matrix (CPython 3.14.4, CPython 3.11, PyPy 3.11). They are not runtime dependencies of the core and are not added to the `dependencies` list.
2. The test suite executes the tutorial notebook with `nbclient` on an `ipykernel` kernel from the isolated environment, in a temporary directory outside the checkout, with the same isolation `tests/test_docs.py` uses (no `PYTHONPATH`, no global Git configuration, `TMPDIR` under pytest's temporary directory).
3. The notebook test lives in `tests/test_notebook.py`. It runs in the CPython 3.14.4 acceptance and scale jobs; the CPython 3.11 / PyPy compatibility job does not select it. `tests/test_ci.py` pins these selections. Jobs that execute the notebook fail, never skip, when the tooling is missing.
4. The notebook is committed with outputs stripped (empty outputs, null execution counts, no execution-specific metadata); a test enforces this.

## Why
The documentation requirements call for one executable research tutorial notebook verified by automated execution (interview rounds 1, 4 and 7), and `AGENTS.md` admits new dependencies only with a decision record (see 011). The `test` extra is where every verification dependency already lives and is what `README.md` tells people to install before running the suite; a separate `docs` extra would split the verification environment and make that instruction incomplete. `nbclient` executes the real notebook file against a chosen kernel and raises on cell errors, which is exactly criterion 5's "executes the actual notebook, fails on any error"; `papermill` and `jupytext` add parameterisation or a second source format the requirements do not ask for, and `nbconvert --execute` pulls in rendering machinery. Stripped outputs avoid committing stale paths, ids and timestamps that would otherwise diverge from every re-execution.

## Alternatives
- A new `docs` extra: keeps notebook packages out of ordinary test installs, but splits the documented verification environment.
- `papermill`, `jupytext` or `nbconvert`: more tooling than the requirement needs.
- Executing the notebook on the compatibility job too: broader evidence, but that job is deliberately narrow. Installation on PyPy 3.11 is already required by point 1, since that job installs the `test` extra; what is not required is notebook execution there, which nobody has verified. Revisit once a PyPy run of `tests/test_notebook.py` is shown to pass.
- Committing outputs: convenient for static reading, but the deliverable is executable and outputs would carry generated ids.

## Consequences
- `pyproject.toml` `test` extra gains three pins; `README.md`'s install line is unchanged.
- `.github/workflows/ci.yml` adds `tests/test_notebook.py` to the scale job's documentation command; the acceptance job discovers it automatically; `tests/test_ci.py` is updated to pin the placement.
- `ruff check .` already lints `.ipynb` files (ruff 0.17.1), so the notebook's code cells are linted with no configuration change.
- Open risk: the pinned versions must be confirmed to install across the matrix in the implementing commit. Installing is distinct from executing: the compatibility job installs the tooling but does not run the notebook.
