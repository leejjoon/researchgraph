# 017: Optional thin operation doors and a same-origin viewer

Status: accepted · 2026-10-21

## Decision

Use the official Python MCP SDK over stdio in the optional `mcp` extra.
Use Starlette and Uvicorn as direct dependencies of the optional `viewer`
extra. Core dependencies remain unchanged. Adapters dispatch the common
operation registry, do not duplicate business validation and never access SQL.
Keep Click's single Commands section. Registry membership is exactly Writes
and Reads; Streams and Servers are separate inventories, documented and tested
in each introducing task, not before implementation. The shared graph and
profile reads bring the graph inventory to 37 (18 writes, 19 reads).

MCP fixes actor, role and read-only authority at launch; agent identity is
required unless human mode is explicit. Full is the default tool preset;
essential is the approved sixteen-tool subset. Read-only intersects either
preset, hides and refuses all writes including init and import. Instructions
are profile agent_guidance only. Results and errors mirror CLI JSON via the
SDK's text/structured result channels, with readOnlyHint from the registry.

Decision [021](021-modern-mcp-structured-results.md) records the observed legacy
wire restriction and current SDK negotiation, faithful array results, bootstrap
and transport-boundary refinement. Real agent-product support remains separately
verified, never inferred from a mock or memory client.

The HTTP adapter serves on loopback as the serving human's git identity,
accepting declared roles per request but never read-only overrides. A launch
token in the fragment is removed by the host and exchanged once in a JSON
POST for an instance-lifetime host-only HttpOnly SameSite Strict cookie.
No query credentials. All protected API, SSE and download routes enforce
the cookie and same-origin Host/Origin checks. No graph in unauthenticated
bootstrap. No-store/no-referrer responses and credential-free logs are required.

Only registered existing regular artifacts under the named node's artifact
root may be downloaded, after decoded-path validation and containment held
through opening. Never resolve then reopen an unchecked path. Reject encoded,
double-encoded and symlink traversal and path-swap races nondisclosingly.
Send attachment disposition with a safe encoded basename, octet-stream and
nosniff. HTML/SVG is inert data, not same-origin executable content.

Package a framework-free custom element and plain ES modules in the wheel,
without a framework or bundler. Safe data-only profile metadata includes unused
types, order, gates, roles and guidance across all four doors. Layout uses
Kahn ranking over only non-retired I1 edges; all other edges are drawn but do
not constrain rank. Append unplaced nodes in created/id order even for invalid
input. Collapse only after ranking. No layout file is written in v0.2.

## Alternatives and rationale

Handwritten MCP framing and a second validation layer diverge from maintained
SDK/core semantics. HTTP-only metadata was rejected: agents and the CLI need
the same safe profile description. Optional extras keep core-only installs
usable; launcher help must not import optional dependencies. Polling events
are specified separately in decision 020. Same-origin embedding avoids a new
cross-origin credential/delegation model; declared identity is not signed auth.

## Consequences

All operation acceptance scenarios must run across API, installed CLI, MCP
and HTTP in both cache modes, retaining transport-specific security/resource
tests. Full includes all registry graph operations; essential is show, ready,
blocked, queue, digest, where, history, evidence, propose, link, attach, claim,
renew, release, complete, fail. Real Claude Code and Codex CLI recordings are
distinct from deterministic replay.

Supported mockup controls map to show/history/evidence/weakest/alternatives/
predictions/notes/subtree, where filters, approve(note), reject(reason),
instruct, one supersede(instruction) for re-run, retire, budget, tag, link(pins),
and expand. Queue comes from queue/blocked, dashboard from digest/stale/ready/
where, refresh from events. Pan/zoom/selection/collapse are presentation only.
Runner capacity, selective edge transfer, budget/lease inheritance,
instruction acknowledgement, diff, scrubber, burn charts, since-last-look,
renderers, notebook preview, standalone app and persisted layout are deferred.
The core never runs scientific work or supplies agent prompts.
