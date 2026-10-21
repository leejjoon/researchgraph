# 023 — Browser components and test tooling

Status: accepted. Date: 2026-10-21.

## Context

Decision 017 and T12 require a packaged framework-free custom element and real
Chromium acceptance, not a DOM mock or a screenshot substituted for behavior.
T11's credential-free `web/` host was intentionally only a transport bootstrap.
The approved component location is `viewer/static/`; two asset conventions would
make packaging and installed-server verification needlessly fragile.

## Decision

Move the host assets into `src/researchgraph/viewer/static/` and add
`research-graph.js` there. Keep the public `/assets/` routes unchanged; remove the
obsolete `web/` files rather than adding aliases. Package and verify the exact
static files from an sdist-built wheel.

Add `playwright>=1.59,<2` to test dependencies for CPython only. PyPy remains an
explicit native/CLI compatibility job, without browser tooling. Required CPython
browser jobs install Chromium explicitly and fail if the package or browser is
missing; tests never skip a designated browser requirement. Use Playwright's
Python API directly, without another pytest plugin, framework or bundler.
Browser binaries are test infrastructure, not runtime package data or viewer
extra dependencies. Do not rewrite the protected lockfile.

`<research-graph src="/api">` accepts a same-origin API-prefix URL without
credentials, query or fragment. It uses the host's already-established cookie;
no token or cookie is copied into attributes, component state or persistent
storage. Authentication and fragment removal finish before mounting. Each
instance owns its fetched profile/graph/digest, filters, selection, viewport and
resource lifetime. Missing or failed requests are explicit errors, not empty
successful graphs. DOM text is assigned as text, never interpolated user HTML.
Keep the existing strict self-only Content Security Policy: shadow roots load
the packaged external stylesheet, not an inline style element or unsafe-eval.
Browser predicates are functions rather than string eval; tests do not bypass
CSP to make the component pass.

The component exports `layoutGraph(graph)`, returning detached nodes with integer
`rank` values and detached edges. Rank uses Kahn's algorithm over non-retired
`blocks`, `derived-from`, `supersedes`, `cites`, and `part-of` edges only. The
created/id-ordered unplaced tail guarantees termination even for hand-built
invalid input. Rendering applies history collapse and filters after ranking;
non-ranking relationships remain drawn. Unused profile declarations still
produce columns, and claimability/gate roles/conditions come from that metadata.

Selection emits a bubbling, composed `node-select` event with the selected node
and id. Read-only presentation interactions never write graph or layout files.
T13–T15 add inspector/actions, review and live monitoring in order; T12 does not
pretend that a connected snapshot is live monitoring or that a mutation starts
a runner.

## Consequences

Behavioral tests use actual served projects, including generated titles, mixed
edge cycles, retired history and custom unused declarations. Two component
instances must remain independent. The suite verifies keyboard controls,
selection, filters, viewport, credential hygiene, inert downloads, resource
cleanup and unchanged authoritative bytes. Layout fallback is deterministic
placement for invalid input, not acceptance of invalid core data or masking a
failed API call. Actual release-clone and external-session evidence remain
separate obligations.
