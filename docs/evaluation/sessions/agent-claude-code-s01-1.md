# Agent session report: Claude Code, attempt s01-1

Formal unassisted ten-step session under `docs/evaluation/agent-session.md`
(signed 2026-10-22). Operator: the maintainer, who is the only person captured;
consent is the operator's own recorded statement. No other person was involved.

| Field | Actual value |
| --- | --- |
| session_id / product | agent-claude-code-s01-1 / Claude Code |
| product_version / model_version | Claude Code 2.1.306; model `claude-fable-5-1` (from the product's result event; provider revision undisclosed) |
| configuration | `claude -p <delivered prompt> --mcp-config mcp-config.json --strict-mcp-config --permission-mode acceptEdits --allowedTools "mcp__researchgraph__* Write Bash(mkdir *) Bash(sha256sum *) Bash(ls *) Bash(cat *)" --output-format stream-json --verbose --max-turns 60`, cwd = the session project; no shared product configuration written; permissions frozen before launch |
| server | `rg --project <project> --as-agent claude-code mcp --preset essential`; role none; read-only off; never `--as-human` |
| source / environment | researchgraph `5adaca9`; `mcp` SDK 2.3.0; CPython 3.14.4; installed `rg` from the project venv |
| consent / protocol_review | operator self-consent; protocol approved by the product owner 2026-10-22 (`e8d4fcb`) |
| preflight | separate diagnostic pass 2026-10-22 (`preflight-2026-10-22.md`); the formal first `show` returned a structured result, which is the negotiation proof |
| seed | `seed.py` with wall-clock seed; seed head `d983d78`; hypothesis `hy-7hpv62c7`; prediction `pr-yfn34lue`; trusted manifest kept in the operator custody store, outside the agent's workspace |
| delivered prompt | project path, the two manifest IDs, then `examples/agent_session/PROMPT.md` unchanged (prompt SHA-256 `75c66ab1c98214e6…`, see custody inventory) |
| timing / outcome | start 2026-10-22T15:02:36Z, end 2026-10-22T15:04:18Z, 102 s elapsed, 18 turns, stop reason end_turn; **completed-unassisted** |
| assistance_interventions | none; the run was headless with stdin closed, so no human input was possible after submission; the product reported `permission_denials: []` |
| transcript | `agent-claude-code-s01-1-transcript.redacted.jsonl` (home directory replaced by `~`; SHA-256 `679797bc68093fd07d2ebde86bf760e1cfab8edac185b0bb5030286828e03177`); original in custody |
| checker_exit_code | 0; `agent-claude-code-s01-1-checker.stdout.json` (`ok: true`, 9 managed commits); stderr empty |
| results | completed Task `ta-ch3h7o3s`, failed Task `ta-nlxzqwti`, Result `re-pii4ae7e`, Claim `cl-4nsivs2a` (proposed, no approvals), Failure `fl-kodr56xx`; artifacts `artifacts/ta-ch3h7o3s/measurement.csv` and `artifacts/re-pii4ae7e/measurement.csv`, both 35 bytes, SHA-256 `7eecb35f3b1475de944ca291535d8b453d18d0355b55f3023ab54f76cd9ceb3b` |
| custody inventory | `agent-claude-code-s01-1-inventory.sha256` in the operator's private custody store; SHA-256 of that inventory file `04582ba3bc40585fbcc79548d66a95fdde8059cda400ed4fd65ec08c7fcd0ddd` |

## Reviewer audit of the ten steps (what the checker does not verify)

Reviewed from the transcript and native files by the operator after the agent stopped.

| Step | Finding |
| --- | --- |
| Graph writes via MCP only | 17 tool calls: one `ToolSearch` (the product loading the deferred tool schemas), 14 `mcp__researchgraph__*` calls, two `Write` calls for the CSVs; no shell `rg`, no edits under `graph/` |
| 1 show | `show hy-7hpv62c7` then `show pr-yfn34lue` |
| 2 propose Task | `addresses → pr-yfn34lue` |
| 3 ready | called; Task listed |
| 4 claim | lease held by `claude-code` |
| 5 artifact and complete | `Write` of exactly `sample,value\ncontrol,2\ntreatment,4\n`; on-disk bytes 35, hash as above; `complete` with that path |
| 6 Result and attach | `fields {runs: 1, held_out: false}`, `derived-from` the Task; second CSV identical; `attach` on the Result's own path |
| 7 Claim | `cites` with `artifact` and `sha256` equal to the on-disk digest; `supports` with `strength: weak`; no `approve` call |
| 8 queue | called; Claim is the sole item |
| 9 second Task and fail | `step`, `check`, `measured`, `error` scalars exactly as the prompt; Failure `fl-kodr56xx` created by the server with `explains` |
| 10 digest and report | called; final report lists the IDs and paths; no further writes |
| Commit authors | nine post-seed commits authored `claude-code <claude-code@agents.researchgraph>`; working tree clean |

Finding for the protocol: the product's first call was `ToolSearch` to load deferred MCP tool schemas. It is the product's own mechanism, not assistance, and should be expected in Claude Code transcripts.

This report establishes the declared-actor, graph and history contract and an
unassisted headless run of the published prompt. It does not authenticate the
model identity beyond the product's own report. Cost reported by the product:
USD 0.92.
