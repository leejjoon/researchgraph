Status: awaiting product-owner review

# Real agent-product session protocol

recorded_product_count: 0

This is an operator protocol and report schema, not a session report. No sessions,
recordings, consent or owner approvals are claimed here. Run one independent,
fresh session in **Claude Code** and one in **Codex CLI**, recording each actual
product/model/configuration separately. Deterministic replay and registration
help syntax are **not a substitute** for either recording or compatibility proof.

## Owner review gate

Do not recruit or record until the product owner has reviewed this protocol and
its capture, stop and disclosure rules. Do not self-approve this table.

| Review | Reviewer | Decision | Date / evidence |
| --- | --- | --- | --- |
| Product-owner protocol review | pending | pending | pending |
| Consent and privacy/capture review | pending | pending | pending |

## Consent, isolation and capture

Before recording, obtain explicit consent from everyone whose voice, screen or
identifiers may be captured. Agree on purpose, access scope, retention/deletion
date, withdrawal contact and deadline before publication. Use synthetic research
only. Record consent references separately from pseudonymous session IDs; never
commit consent forms or personal contact details. Stop immediately on withdrawal
or accidental disclosure. Preserve the stop as an outcome without publishing the
sensitive material.

Use a dedicated OS account/workspace and isolated product configuration with only
the required credentials and server. Claude local registration is project-scoped;
Codex registration normally changes shared `~/.codex/config.toml`, so do not use a
shared personal configuration. Inspect installed product help for the supported
isolation mechanism; do not invent flags or overwrite an existing registration.
The server's declared identity is not a filesystem sandbox. Keep the source
checkout, checker and trusted manifest in an operator-controlled location outside
the agent's writable workspace, enforced with OS permissions/account separation.
The agent needs only the two seed IDs, project path and prompt, not write access
to the trusted manifest. Retain an independent trusted manifest copy.

Capture the actual product conversation/tool-call export where supported, plus a
continuous terminal/screen recording from preflight through final agent response.
If no export is available, retain the recording and a faithful timestamped
transcription with tool requests/results; document capture gaps. Do not manufacture
empty transcripts or replace errors with summaries. Keep stdout/stderr distinct
for the independent checker. Record start/end UTC timestamps and monotonic elapsed
time. Disable unrelated notifications and close personal windows before capture.

Before sharing, review every frame, transcript, configuration and log for API keys,
auth headers, tokens, cookies, credential URLs, environment values, personal paths,
Git email and unrelated prompts. Redact values, not the existence of permission or
protocol errors. Keep restricted originals only with consent; hash originals and
redacted derivatives separately and record the redaction mapping/reason. Never
commit secrets, private raw recordings or credentials. If safe evidence cannot be
retained, report that limitation instead of calling the run verified.

## Operator preparation (not timed agent work)

Use the already installed package with its MCP extra, an absolute installed `rg`
(not ripgrep), and the matching Python interpreter. Resolve the source checkout
and record its commit, prompt/seed/check hashes, package version, interpreter
version and executable paths. The APIs are `seed(root, *, actor, now)` and
`check(root, manifest) -> summary`; the latter raises `AssertionError` on invalid
sessions. The CLI commands below match the example scripts.

For each product choose a different unused absolute project and manifest path.
Example for Claude Code (use a new `codex-session` root/manifest for Codex CLI):

```sh
mkdir /tmp/researchgraph-claude-session
git -C /tmp/researchgraph-claude-session init
git -C /tmp/researchgraph-claude-session config user.name "Session researcher"
git -C /tmp/researchgraph-claude-session config user.email "session@example.test"
/absolute/path/to/venv/bin/python /absolute/path/to/researchgraph/examples/agent_session/seed.py \
  /tmp/researchgraph-claude-session \
  --manifest /absolute/operator-only/evidence/claude-seed.json
```

The human operator supplies the consent-safe Git identity. `seed.py` refuses to
overwrite a manifest; its optional `--now RFC3339` sets the seed clock, otherwise
whole-second UTC wall time is used. It creates and approves the Hypothesis and
Prediction in the `research` profile. Retain its printed manifest, `seed_head`,
`baseline` hashes, `seed_actor`, `seed_time`, `hypothesis` and `prediction`. Review
that seed before launch. No human graph writes are allowed after this boundary.

Register the server using the actual installed absolute executable and project:

```sh
claude mcp add --transport stdio --scope local researchgraph -- \
  /absolute/path/to/venv/bin/rg --project /tmp/researchgraph-claude-session \
  --as-agent claude-code mcp --preset essential
codex mcp add researchgraph -- \
  /absolute/path/to/venv/bin/rg --project /tmp/researchgraph-codex-session \
  --as-agent codex-cli mcp --preset essential
```

These forms are documented in the user manual; syntax was previously checked
with Claude Code 2.1.306 and Codex CLI 0.155.1 help, not live compatibility. Record
the actual installed versions and actual selected model/version; do not copy those
historical versions as the session values. When a provider does not disclose an
exact model revision, record the visible identifier and `undisclosed` revision,
not a guessed one. Record relevant product permissions, approval policy, system
instructions, enabled integrations and redacted configuration content/hash.

Launch identity, role and read-only are fixed for the server process; tools cannot
override them. `--as-agent NAME[:MODEL[:VERSION]]` can encode known model/version;
record the exact value used. Never use `--as-human` for the agent phase.
`essential` exposes the ten-step operations but not initialization/human review.
`full` is the default and exposes all registry operations; choosing it must be
recorded, not switched midway to repair a failure. Global `--read-only` intersects
either preset, hides/refuses writes and is appropriate only for a separately
labelled read-only diagnostic, not this write-workflow success criterion. Record
any launch `--role` and why; do not supply authority overrides through tool args.

## Compatibility preflight: real products, real negotiation

Run operator-directed compatibility diagnostics on a **separate disposable
diagnostic project and conversation**, before creating the formal session seed
described above. Diagnostic prompting is assisted setup, never one of the two
required unassisted recordings. After a passing diagnostic, create a fresh formal
seed/manifest and a fresh product conversation, retaining the reviewed product,
model, permissions and preset; record the deliberate project-path change. Deliver
only the unchanged session prompt to that conversation. During the formal attempt,
observe negotiation and its first `show` passively from the actual capture; do not
ask for extra diagnostic calls, supply hints or repair configuration after seed.
If formal negotiation fails, retain a failed attempt rather than coach it through.

Within each actual product's `/mcp` view inspect connection/discovery and retain
its real output. Verify modern **MCP 2026-07-28 discovery negotiation**, using
available consent-safe client/server diagnostics to establish the negotiated
version. A server listing or successful registration alone is insufficient.
Have that product invoke `show` for the seed Hypothesis and retain the actual
request/result, including arbitrary-JSON structured content. Legacy
initialize-only refusal, missing credentials/model access, unavailable protocol
evidence or incompatible structured results are real findings. Do not downgrade
the server, wrap results, replace the client with an SDK replay or claim a pass
from help output. Record preflight pass/failure/indeterminate and the reason.
Allow at most five minutes for preflight; stop this attempt on failure or missing
negotiation proof. A changed configuration requires a separately identified fresh
attempt and fresh seed, not silent repair of the original outcome.

## Unassisted ten-step run

Give the product the **unchanged** `examples/agent_session/PROMPT.md`, the absolute
project path and the two seed IDs. Record the exact delivered prompt/hash and any
product wrapper instructions. Start the task clock and continuous capture before
submission. The approved human seed precedes this clock; there is no human
execution or coaching during these ten steps:

1. Show the approved Hypothesis (read Prediction as needed).
2. Propose a Task addressing that Prediction.
3. Check `ready` includes the Task.
4. Claim it with the launch-fixed agent identity.
5. Write the exact prompt's measurement CSV in its owned Task directory and
   complete with that artifact; verify done and no lease.
6. Propose a Result derived from the Task, write its separate owned CSV and
   attach it; retain the actual returned SHA-256.
7. Propose the Claim with the prompt's `cites` artifact/hash and weak `supports`
   relationship; do not approve it.
8. Confirm the Claim is the sole pending queue item.
9. Propose and claim the second Task, then fail with scalar
   `step="validate second sample"`, `check="sample count must be at least 2"`,
   `measured="1"`, `error="insufficient samples"`; retain the native Failure.
10. Read digest, report the terminal Task/Result/Claim IDs and artifact paths,
    and stop writing. The Claim remains proposed for later human review.

The prompt is authoritative for exact bytes and arguments; do not replace it with
a paraphrased easier exercise. All graph operations use MCP, commits stay enabled,
and artifact bytes use the product's ordinary local filesystem capability. Do not
edit managed YAML. Explicit tool clocks, if supplied, must increase by whole UTC
seconds after the seed; clock failures count as failures.

Never coach after start: no hints, corrected IDs/arguments, graph edits, hidden
prompts, retries by the operator or supplied solutions. Record every interaction
in `assistance_interventions`, including required permission clicks, clarifications
and safety stops, with time, initiator, content and effect. Zero assistance must
be supported by capture, not asserted by default. An intervention makes the attempt
assisted; retain it as such rather than relabel it unassisted.

Stop at completion, withdrawal, privacy/safety issue, product crash, persistent
protocol failure, or 30 minutes after prompt submission, whichever comes first.
Record elapsed time, last completed step, tool errors and outcome:
`completed-unassisted`, `completed-assisted`, `failed`, `timed-out`, `withdrawn`
or `capture-incomplete`. Timeout and failures remain outcomes; do not restart the
clock or omit unsuccessful attempts. No post-session correction of the graph.

## Independent audit after the agent stops

An operator independent of the agent runs the trusted checker, with the trusted
manifest and source. Example (output paths must be fresh, operator-controlled):

```sh
/absolute/path/to/venv/bin/python /absolute/path/to/researchgraph/examples/agent_session/check.py \
  /tmp/researchgraph-claude-session \
  --manifest /absolute/operator-only/evidence/claude-seed.json \
  > /absolute/operator-only/evidence/claude-checker.stdout.json \
  2> /absolute/operator-only/evidence/claude-checker.stderr.log
checker_exit_code=$?
printf '%s\n' "$checker_exit_code" > /absolute/operator-only/evidence/claude-checker.exit.txt
```

Do the equivalent for the independent Codex root/manifest. Retain nonzero exits and
errors. A failed or incomplete session may legitimately fail this audit. CLI
success prints JSON with `ok: true`; invalid input/session prints `ok: false` and
exits 1. Do not infer success merely from an output file's existence.

The checker reads native YAML, artifact bytes and Git history, not SQLite or the
agent's returned IDs. It requires unchanged approved seed records, exact session
relationships, committed artifacts, terminal Tasks and execution events, a
Failure with step/check, and the proposed Claim. It rejects dirty managed paths
and audits every reachable managed post-seed commit, including human edits later
restored. It intentionally does not support merged histories or extra graph
nodes/relationships. Preserve failures rather than weakening its contract.

Declared actors and Git authors are not authenticated human/model identities.
The checker does not prove physical non-assistance or product identity; actual
capture, version/configuration evidence and independent review are also required.

## Report template and retention

The fields below are a schema, not populated sessions. Create reports only for
actual attempts; keep the count at zero until genuine recordings are reviewed.

| Field | Required actual evidence |
| --- | --- |
| session_id / product | Unique pseudonymous attempt ID; Claude Code or Codex CLI |
| product_version / model_version | Observed versions and source; undisclosed revision noted |
| configuration | Redacted content/hash, launch command/identity/preset/role, permissions |
| source / installed environment | Source commit, installed rg/Python paths and versions |
| consent / protocol_review | Restricted consent reference and owner decision reference |
| preflight | Negotiation version, actual show result, pass/failure/indeterminate |
| timing / outcome | UTC start/end, elapsed seconds, stop reason, last step |
| assistance_interventions | Complete timestamped interventions, or capture-supported none |
| transcript / recording | Actual paths, capture method and gaps, privacy-review disposition |
| seed / native state | Trusted manifest/hash, seed/head IDs, retained native Git snapshot |
| checker_exit_code | Actual exit, stdout/stderr/exit-file paths and hashes |
| results / logs | All result IDs, artifact hashes, prompt/config/tool/export/log hashes |
| findings / review | Reproduction, severity, disposition, independent reviewer decision |

Name evidence `agent-<product>-<pseudonymous-id>-<attempt>-<kind>.<ext>` under an
access-controlled evidence directory. Keep originals immutable, plus a SHA-256
inventory of **every** retained file (all results, logs, manifests, recordings,
transcripts, checker outputs, native snapshot and redacted derivatives). For an
actual file, `sha256sum /absolute/path/to/file` produces its inventory entry;
record relative evidence names, byte sizes and redaction lineage. Do not use
empty fake transcripts or placeholder hashes as evidence. Set the consent-agreed
retention deadline and custodian before collection; delete restricted originals
and backups at that deadline, recording deletion receipts without private data.
Only consent-safe actual reports and approved derivatives may be reviewed for a
later commit. Hashes establish byte identity, not authenticity or owner approval.
No recording or review has been performed by creating this protocol.
