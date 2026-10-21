# 021: Current MCP negotiation and faithful structured results

Status: accepted · 2026-10-21

## Evidence

The approved adapter uses the official optional SDK and preserves ordinary CLI
JSON, including arrays and omission versus explicit null, in both result channels.
The installed official Python SDK is 2.3.0 (Python >=3.10). Its typed low-level
handlers replace the old decorator API. Its current CallToolResult Python model
admits arbitrary JSON, but a real SDK client `initialize()` negotiates the older
wire: returning a `where` array then fails the SDK's result validation with
`structuredContent: Input should be a valid dictionary`. This is an actual failed
round trip, not an assumption about the library.

The same SDK's `discover()` negotiates current 2026-07-28 MCP and preserves the
array directly in text and structuredContent. The real memory-client tests pass
with that negotiation. An object wrapper would change the promised data shape;
text-only success would omit a required result channel.

## Decision

Use the current official SDK (`mcp>=2.3,<3`) and its current discovery negotiation
for initialization, tool discovery and stdio calls. Require that wire contract
for all tools. Legacy initialize-only connections receive an explicit structured
protocol error rather than partially working until an array is returned. Do not
implement handwritten framing, an old-SDK shim, an array wrapper or a text-only
fallback. Installed-stdio cancellation/shutdown and real Claude Code/Codex
interoperability still require direct proof. A memory client does not establish
agent-product support or Gate 2.

The launch fixes project, actor, role and read-only. Tool input contains operation
arguments plus optional evaluation `now` and write `commit`; no authority/output
options. Boundary checks reject unknown authority keys and incomplete transport
arguments, but business validation, permission precedence and SQL handling stay
in public core methods. Preserve omitted value arguments; empty repeated Click
collectors map to empty arrays. Init/import destination arguments may only name
the selected root. A selected uninitialized destination may bootstrap, with empty
initial instructions until a profile exists; existing invalid projects are not
silently treated as uninitialized. Instructions otherwise contain only profile
guidance, never core prompts.

## Consequences

The task's initialization step uses current discovery, not a claim that legacy
`initialize()` supports the required arrays. Tests explicitly distinguish those
negotiations. Actual product versions/configurations must be checked against this
contract before release, and incompatible clients remain release blockers rather
than being counted as successful recordings. Core installs do not gain an SDK
dependency; help stays lazy and missing extras have an actionable remedy.
