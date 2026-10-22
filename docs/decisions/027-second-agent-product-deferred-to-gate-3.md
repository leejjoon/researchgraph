# 027: The second agent-product recording is deferred to Gate 3

Status: accepted · 2026-10-23 · amends the Gate 2 agent-product cell for v0.2 only; keeps decision 021

## Decision
1. Gate 2's "scripted agent session passes on two agent products" is satisfied for v0.2 by **one** recorded unassisted session (Claude Code 2.1.306, `docs/evaluation/sessions/agent-claude-code-s01-1.md`) plus the **retained failed compatibility attempts** for Codex CLI 0.155.1 and 0.163.0 (`sessions/preflight-2026-10-22.md`). The Codex cell is recorded as **deferred, failed attempts retained**, never as passed.
2. Decision 021 stands: the server keeps requiring MCP 2026-07-28 negotiation; no downgrade, wrapper, text-only fallback or client substitution.
3. The second-product recording is carried forward as an explicit debt to be closed before Gate 3 (end of v0.3): either a Codex CLI release that negotiates 2026-07-28 over stdio, another product that does, or a later decision record that narrows the refusal, followed by a formal unassisted session under the signed protocol.
4. A v0.2 tag may be made with this deferral recorded in the evidence index and the Gate 2 closure note.

## Why
Codex CLI 0.163.0 negotiates 2025-06-18 over stdio in every configuration tried, including its `mcp_2026_07_28` feature flag and the `CODEX_MCP_PROTOCOL_VERSION` variable; its 2026-07-28 support exists only in the HTTP client path. The only ways to record it now would change product behaviour (a new decision narrowing 021) or add an HTTP transport, both outside a release gate's remit. Waiting indefinitely would hold v0.2 open on a vendor timeline while every other cell is recorded or explicitly substituted. Deferring with the failed attempts retained keeps the ledger rule "never mark incomplete external cells complete" true.

## Alternatives
- Narrow 021 now (accept legacy negotiation for object-shaped results): a product change made under release pressure; kept as a v0.3 option.
- Amend Gate 2 to "two products attempted": rejected by the committee as counting a failed cell as passed.

## Consequences
- `tasks/v0.2.md` Gate 2 paragraph and `docs/evaluation/release-evidence.md` gain the deferral wording; `tasks/backlog.md` gains the debt.
- Gate 2 closure now waits only on the final clean-clone run at the candidate SHA, the closure note and the tag.
