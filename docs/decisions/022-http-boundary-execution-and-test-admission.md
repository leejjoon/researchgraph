# 022 — HTTP boundary execution and test admission

Status: accepted. Date: 2026-10-21.

## Context

Decision 017 defaults the viewer to the serving Git human, while the four-door
contract requires real agent permission scenarios too. A request-supplied actor
would violate fixed launch authority; relabelling an agent scenario as human
would falsify parity. The existing common CLI already has `--as-agent`.

The selected Starlette 1.9.0 and Uvicorn 0.54.0 are present through the optional
MCP installation, but HTTPX is not. Depending on those incidental dependencies
would make the viewer-only installation and HTTP tests unreliable.

## Decision

HTTP keeps the serving Git human as its default. An explicit launch
`--as-agent NAME[:MODEL[:VERSION]]` may instead fix a declared agent for API
consumers and permission tests. Neither mode lets requests choose an actor or
project, or disable read-only. This adds a less-privileged launch mode, not an
authority override or signed authentication. Browser review normally uses the
human default. Roles remain self-declared per request, as already specified.

Use the optional `viewer` extra with direct `starlette>=1.9,<2` and
`uvicorn>=0.54,<1` requirements. Add `httpx>=0.28.1,<1` for the independent real
socket adapter and `httpx2>=2.16,<3` for Starlette's maintained test client to the
test extra. Installed Starlette 1.9 metadata advertises HTTPX2 for its full extra;
its testclient source warns that the classic HTTPX fallback is deprecated.
Explicit admission prevents MCP's incidental HTTPX2 dependency from hiding that
viewer-only test gap. Neither a deprecated fallback nor the unrelated full extra's
template/multipart dependencies are needed. No core runtime dependency is
added; launcher help must not import either optional stack. Test-only imports
remain lazy in core-only compatibility fixtures. Do not modify the protected
user lockfile to install these extras.

Current `GET /api/graph` returns the graph from `ChangeSource.snapshot`, with
its associated cursor in `X-Researchgraph-Cursor`; it must not independently
scan a graph and then attach a later observation's cursor. Historical as-of
reads retain their ordinary limitations and do not advertise a live cursor.
Requests and polling share one serialized Project owner and change source.

The HTTP adapter uses the same public dispatcher and core methods as MCP.
Structured/repeated read query values are JSON; mutation bodies are objects.
Role is transport context, not a stored field. Actor/project/read-only overrides
are authority failures, and initializer destinations cannot escape the selected
root. Runtime permission and data validation remain in the core.

## Consequences

All meaningful public JSON operation scenarios can run through real HTTP with
the same launch actor, including human-only and wrong-role failures. The default
viewer identity, cookie/Host/Origin rules, inert artifact opening, registry count
and core file format are unchanged. Intrinsic CLI syntax, Python-only values and
private fault instrumentation need explicit inventory dispositions, not fake
HTTP routes or renamed assertions. Authentic model and researcher evidence is
still separate from transport fixtures.
