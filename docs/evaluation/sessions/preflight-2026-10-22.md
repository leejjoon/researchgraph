# Compatibility preflight, 2026-10-22

Assisted setup under `docs/evaluation/agent-session.md`, not a formal recording.
Source `e8d4fcb`; `mcp` SDK 2.3.0; installed `rg` from the project venv. Each
attempt used its own disposable diagnostic project seeded with `seed.py --now
2026-10-22T00:00:00Z`. Prompt: call `show` on the seed hypothesis through the
researchgraph MCP server, report the raw result, no other tools, no writes. No
shared product configuration was modified: Claude Code used an inline
`--mcp-config` file with `--strict-mcp-config`; Codex CLI used `-c
mcp_servers.*` overrides. Raw captures, seed manifests, wire logs and a SHA-256
inventory are in the operator's custody store
(`preflight/evidence/inventory-2026-10-22.sha256`); nothing here is a formal
session report and the counts in the protocols stay at zero.

| Product | Version | Model | Negotiation | Outcome |
| --- | --- | --- | --- | --- |
| Claude Code | 2.1.306 | claude-fable-5-1 | structured `show` result returned (node, edges, flags); `permission_denials: []` under an allow rule for `mcp__researchgraph__show` | **pass**, 18 s; the product made one `ToolSearch` call to load the deferred tool schema before `show` |
| Codex CLI | 0.155.1 | not reported by `exec` | tool discovered and called with the correct id; server refused: "MCP 2026-07-28 discovery negotiation is required; legacy structuredContent cannot preserve array results" | **fail**, negotiation |
| Codex CLI | 0.163.0 | gpt-5.6-sol (from turn metadata) | wire capture: client `initialize` at `2025-06-18`, server answers `2025-06-18`, `tools/list`, `tools/call show`, same refusal | **fail**, negotiation; four attempts |

Codex 0.163.0 attempts: plain; `--enable mcp_2026_07_28`; the flag plus
`-c features.mcp_2026_07_28=true`; both plus `CODEX_MCP_PROTOCOL_VERSION=2026-07-28`.
All negotiated `2025-06-18` over stdio. `codex features list` shows
`mcp_2026_07_28` as "under development, false"; strings in the binary place the
2026-07-28 negotiation (`MCP-Protocol-Version` header, `server/discover`) in the
HTTP client and the Codex Apps path, not the stdio client.

## Disposition (product owner, 2026-10-22)

Decision 021 stands: the server keeps requiring 2026-07-28 negotiation for every
call, and no downgrade, wrapper, text-only fallback or client substitution was
made. Codex CLI is recorded as a failed compatibility attempt. Its Gate 2 cell
remains open until a Codex release negotiates 2026-07-28 over stdio, or the
maintainer chooses another second product, or a later decision record narrows
the refusal. Claude Code proceeds to the formal unassisted session.
