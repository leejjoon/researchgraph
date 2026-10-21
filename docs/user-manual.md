# researchgraph user manual

This describes researchgraph 0.0.1, the v0.1 specification milestone, and bundled
profiles research 1.0 and tasks 1.0. It is descriptive, not normative: [the
specification](../spec/operations.md) remains the contract. Report discrepancies;
never change the specification just to make documentation pass.

Start with this manual for everyday work, the [developer manual](developer-manual.md)
for integrations, or the [research tutorial](research-tutorial.ipynb) for a synthetic
experiment. The unchanged [quickstart](quickstart.md) runs three Tasks with the
unsupported reference runner and tasks profile; the notebook instead uses research,
explicit human review points, an agent Task, saved results and a supported API.

## Prerequisites and installation

Use Python 3.11+, Git, a C compiler and uv. CI checks CPython 3.14.4, CPython 3.11
and PyPy 3.11 functionality; that does not claim every platform or free-threaded
build works. Two native extensions are compiled for your interpreter/platform ABI.
There is no stable-ABI promise or package-index publication assumption.

From a fresh source checkout, run this shell block there. UV_PYTHON may select an
installed interpreter (the tests set it to their interpreter). The PyPy constraint
selects an existing jsonschema dependency, not a new core dependency.

<!-- rg-example: source-install; part=1; runnable -->
```sh
uv venv --python "${UV_PYTHON:-python3}" .venv
uv pip install --python .venv/bin/python -e . "rpds-py==0.25.1; platform_python_implementation == 'PyPy' and python_version == '3.11'"
.venv/bin/rg --version
```

Always use the installed `.venv/bin/rg`, or its absolute path: unqualified `rg`
often means ripgrep. Export `RG` with that absolute path (for example,
`export RG="$PWD/.venv/bin/rg"` from the checkout). Install the `jq` command-line
JSON processor, then run the following shell blocks in order from the same new,
empty working directory **outside the checkout**. Parts share files, so they may
run in separate shells. Provisioning can download build
and package dependencies; the following workflows require no network. Git identity
is configured in each demo only, never globally. The assertions and printed JSON
are expected results, not precomputed IDs. `jq` extracts generated IDs and checks
responses; all researchgraph operations run through the installed CLI.

## Mental model

A project stores typed nodes and directed typed edges in YAML under Git. Files are
authoritative; SQLite is a disposable acceleration cache. Nodes have generated IDs,
title/body, fields, author, status, approvals, artifacts, budget, usage and tags.
An edge's direction matters: a Hypothesis answers a Question; a Prediction tests a
Hypothesis with an expected outcome; a Claim cites a Result and supports or
contradicts a Hypothesis. `blocks` and `part-of` structure work; cycles are rejected
where the profile/core requires acyclicity. Supersession retires an old node and
links its replacement rather than erasing history.

Profiles pin types, schema and gates into a project. In research, Hypothesis,
Prediction and Claim begin proposed and need human activation. Any declared human
can satisfy these gates: the declared owner and reviewer roles do not make them
reviewer-only. Agents cannot approve these gates. Only Task is claimable. The tasks
profile has Task, Result and Failure, no activation gates; use it for straightforward
work rather than pretending it is the research lifecycle.

Identity and role are **self-declarations, not authentication**. CLI human identity
comes from repository Git name/email; agents supply a name/model/version. Neither
protects filesystem access. A lease reserves a ready Task for an actor for a bounded
period. A running Task with an expired lease can be reclaimed; a live lease cannot.
Budget excludes over-budget work from readiness; the core neither meters nor runs
work. Consumers report usage explicitly.

Artifact records describe existing bytes: relative path, SHA256, byte size, adding
actor and time. `attach` registers, never creates or moves files. Save files under
`artifacts/<owning node id>/`. Evidence follows live cites/derived-from relations;
it is graph provenance, not an assessment of scientific validity.

## Research workflow: review, work, preserve evidence

First initialize, propose and link a Question, Hypothesis and Prediction. Each CLI
call uses a fixed advancing whole-second UTC clock and returned IDs. Human review
here is simulated declared identity; an automated successful run is not a claim
that a person reviewed the science. Inspect the question and hypothesis before
approving the latter, then inspect the predicted outcome before its approval.

Run all nine steps in order from the same working directory. JSON files retain
the generated IDs between steps. Each `jq -e` check prints `true` on success and
stops the example if the expected condition is false.

### 1. Create a project and a small CLI helper

Create a disposable Git repository with a local demo identity. The helper file
sets the project path and defines `call TIME COMMAND ...`, which invokes the real
CLI with JSON output and an explicit UTC timestamp. Later blocks load it with
`. ./user-cli.sh`, so they also work in separate shells.

<!-- rg-example: user-lifecycle; part=1; runnable -->
```sh
set -eu
cat > user-cli.sh <<'SH'
set -eu
: "${RG:?Export the absolute path to the installed .venv/bin/rg}"
root="$PWD/research-demo"
call() {
    time="$1"; shift
    "$RG" --project "$root" --json --now "2026-10-20T${time}Z" "$@"
}
SH
. ./user-cli.sh
mkdir "$root"
git -C "$root" init
git -C "$root" config user.name 'Tutorial human'
git -C "$root" config user.email human@example.test
git -C "$root" config commit.gpgsign false
call 00:00:02 init | jq -e '.profile == "research"'
```

### 2. Ask a question and approve a hypothesis

Propose the research Question and a possible answer. The `answers` edge points
from the Hypothesis to the Question. After reviewing both, the declared human
approves the Hypothesis, changing it from proposed to active.

<!-- rg-example: user-lifecycle; part=2; runnable -->
```sh
. ./user-cli.sh
call 00:00:04 propose Question 'Does adding two shift the mean?' > question.json
question=$(jq -er '.id' question.json)
call 00:00:06 propose Hypothesis 'The shift is two' > hypothesis.json
hypothesis=$(jq -er '.id' hypothesis.json)
call 00:00:08 link answers "$hypothesis" "$question"
call 00:00:10 approve "$hypothesis" | jq -e '.status == "active"'
```

### 3. State the prediction to test

Give the hypothesis a concrete expected outcome. The `tests` edge records that
expectation and connects the Prediction to the Hypothesis. Review the prediction
before approving it.

<!-- rg-example: user-lifecycle; part=3; runnable -->
```sh
. ./user-cli.sh
hypothesis=$(jq -er '.id' hypothesis.json)
call 00:00:12 propose Prediction 'Mean difference is two' > prediction.json
prediction=$(jq -er '.id' prediction.json)
call 00:00:14 link tests "$prediction" "$hypothesis" \
    --fields '{"expected": "paired mean difference = 2"}'
call 00:00:16 approve "$prediction" | jq -e '.status == "active"'
```

### 4. Create a Task and check readiness

Create the work item and link it to the Prediction it addresses. The readiness
query confirms this Task can be claimed; it does not schedule or execute it.

<!-- rg-example: user-lifecycle; part=4; runnable -->
```sh
. ./user-cli.sh
prediction=$(jq -er '.id' prediction.json)
call 00:00:18 propose Task 'Measure fixed paired data' > task.json
task=$(jq -er '.id' task.json)
call 00:00:20 link addresses "$task" "$prediction"
call 00:00:22 ready | jq -e --arg task "$task" 'any(.items[]; .id == $task)'
```

### 5. Claim the Task, save data, and complete it

Switch to a distinct declared agent for the claim and completion. Write a tiny
constructed dataset under the Task's artifact directory, then register it while
completing the Task. The shift of two is illustrative, not scientific validation.

<!-- rg-example: user-lifecycle; part=5; runnable -->
```sh
. ./user-cli.sh
task=$(jq -er '.id' task.json)
call 00:00:24 --as-agent tutorial-worker claim "$task" | jq -e '.status == "running"'
relative="artifacts/$task/paired.json"
mkdir -p "$root/artifacts/$task"
printf '%s\n' '{"baseline": [1, 2, 3], "shifted": [3, 4, 5], "delta": 2}' \
    > "$root/$relative"
call 00:00:26 --as-agent tutorial-worker complete "$task" 'Paired shift measured' \
    --artifact "$relative" --usage '{"tokens": 0}' | jq -e '.status == "done"'
```

### 6. Record a Result with its own artifact

Return to the declared human identity by omitting `--as-agent`. Create a Result
derived from the completed Task. Copy the saved data into the Result's own
directory and attach it there: `attach` records existing bytes, not a file copy.

<!-- rg-example: user-lifecycle; part=6; runnable -->
```sh
. ./user-cli.sh
task=$(jq -er '.id' task.json)
relative="artifacts/$task/paired.json"
call 00:00:28 propose Result 'Measured paired shift' --fields '{"runs": 1}' > result.json
result=$(jq -er '.id' result.json)
call 00:00:30 link derived-from "$result" "$task"
result_relative="artifacts/$result/paired.json"
mkdir -p "$root/artifacts/$result"
cp "$root/$relative" "$root/$result_relative"
call 00:00:32 attach "$result" --artifact "$result_relative" \
    | jq -e '.artifacts | length > 0'
```

### 7. Cite the Result and approve a Claim

The Claim cites the Result and supports the Hypothesis. Review the Result's saved
bytes and these relations before approval. Approval makes the Claim active, not
done; the validator checks provenance links, not the truth of the claim.

<!-- rg-example: user-lifecycle; part=7; runnable -->
```sh
. ./user-cli.sh
result=$(jq -er '.id' result.json)
hypothesis=$(jq -er '.id' hypothesis.json)
call 00:00:34 propose Claim 'This constructed sample has shift two' > claim.json
claim=$(jq -er '.id' claim.json)
call 00:00:36 link cites "$claim" "$result"
call 00:00:38 link supports "$claim" "$hypothesis"
call 00:00:40 approve "$claim" | jq -e '.status == "active"'
```

### 8. Inspect evidence and the released lease

Confirm the Claim's evidence includes the Result and its artifact. Then check
that completing the Task cleared its lease. No scheduler is hidden in these steps.

<!-- rg-example: user-lifecycle; part=8; runnable -->
```sh
. ./user-cli.sh
claim=$(jq -er '.id' claim.json)
result=$(jq -er '.id' result.json)
task=$(jq -er '.id' task.json)
call 00:00:42 evidence "$claim" | jq -e --arg result "$result" \
    'any(.items[]; .id == $result and (.artifacts | length > 0))'
call 00:00:44 show "$task" | jq -e '.lease == null'
```

### 9. Replace a follow-up without erasing history

Propose a separate follow-up Task, then supersede it with a better-scoped Task.
The replacement has a new ID; the original remains available in history. The
last command prints a compact receipt with the project, Claim, and replacement IDs.

<!-- rg-example: user-lifecycle; part=9; runnable -->
```sh
. ./user-cli.sh
claim=$(jq -er '.id' claim.json)
call 00:00:46 propose Task 'Plan a follow-up' > followup.json
followup=$(jq -er '.id' followup.json)
call 00:00:48 supersede "$followup" --title 'Repeat with a larger sample' > replacement.json
replacement=$(jq -er '.id' replacement.json)
test "$replacement" != "$followup"
call 00:00:50 history "$followup" | jq -e 'length > 0'
jq -n -c --arg project "$root" --arg claim "$claim" --arg replacement "$replacement" \
    '{project: $project, claim: $claim, replacement: $replacement}'
```

## Servers

`rg mcp` is an optional stdio server, outside the graph-operation inventory.
Install the source extra with `uv pip install -e '.[mcp]'`; a core-only install
still supports launcher help and reports this remedy on invocation. Select the
project and launch either as an explicit agent or with explicit human mode.
Actor, role and read-only are fixed for that process; no tool can override them.
Use MCP 2026-07-28 discovery negotiation. Older initialize-only clients cannot
carry the required arbitrary-JSON structured results and receive an explicit
protocol refusal, not wrapped or partial results. Actual Claude Code/Codex
compatibility and recordings are separate release evidence, not inferred here.
No core prompts are supplied: instructions are only profile guidance (empty
before bootstrapping a selected uninitialized destination).

`rg serve` is the optional loopback HTTP/SSE server, not a graph operation.
Install `researchgraph[viewer]` (from source, `uv pip install -e '.[viewer]'`).
It binds an ephemeral loopback port and prints a launch URL; `--no-open` avoids
opening a browser. The URL fragment is a one-use credential: keep it private.
The static host removes the fragment and exchanges it for a host-only HttpOnly
SameSite Strict cookie. Credentials are never query parameters. Unauthenticated
pages contain no graph; authenticated reads need the cookie and matching Host,
and writes additionally need the exact Origin. Access logs are disabled.

The launch fixes project, read-only and Git human identity by default; an explicit
`--as-agent` fixes that less-privileged identity instead. Requests may declare a
role, never replace the actor or disable read-only. This is declared identity,
not authentication of a person/model or an OS security sandbox. Restarting the
server invalidates credentials and observed-event cursors. Ctrl-C closes its owner.
Current graph reads carry their associated `X-Researchgraph-Cursor`; events replay
retained successors and reset requires a fresh snapshot. Downloads are only
registered node-owned regular files, served as inert attachments, not executable
HTML/SVG. This transport bootstrap is distinct from the later graph viewer.

### MCP presets and client registration

`--preset full` is the default and exposes every graph-registry operation.
`--preset essential` exposes exactly `show`, `ready`, `blocked`, `queue`,
`digest`, `where`, `history`, `evidence`, `propose`, `link`, `attach`, `claim`,
`renew`, `release`, `complete` and `fail`. Read-only intersects either preset;
an unlisted operation is not a back door to the full set. Seed prerequisites
as a human before an essential agent session: it does not expose `init` or
human review operations.

Use absolute paths to the installed console script and the selected project,
not another executable named `rg`. The following commands change the client's
configuration; they are operator setup, not a CI session or recorded evaluation.
Claude's local scope is project-specific; Codex's CLI writes its configuration
(normally `~/.codex/config.toml`, shared with other Codex surfaces). Use isolated
evaluation configuration rather than overwriting a shared server registration.

<!-- rg-example: mcp-registration; part=1; fragment -->
```sh
claude mcp add --transport stdio --scope local researchgraph -- \
  /absolute/path/to/venv/bin/rg --project /absolute/path/to/project \
  --as-agent claude-code mcp --preset essential
codex mcp add researchgraph -- \
  /absolute/path/to/venv/bin/rg --project /absolute/path/to/project \
  --as-agent codex-cli mcp --preset essential
```

Check the server with each product's `/mcp` view. Configuration or discovery
success alone does not prove tool calls can negotiate the required protocol.
The command forms were checked with Claude Code 2.1.306 and Codex CLI 0.155.1
`mcp add --help`, and the official [Claude Code MCP reference](https://code.claude.com/docs/en/mcp)
and [Codex MCP reference](https://developers.openai.com/codex/mcp/).
These are syntax checks, not successful registration, model access, protocol
compatibility or unassisted recordings. Record actual product/model versions,
configuration and transcripts separately for Gate 2; the deterministic replay
does not substitute for either real product session.

The [ten-step prompt](../examples/agent_session/PROMPT.md) includes human seed
and independent check commands. Run `seed.py PROJECT --manifest MANIFEST.json`
before the session and `check.py PROJECT --manifest MANIFEST.json` afterward,
using absolute script paths and an installed package. Keep the trusted manifest
outside the agent's writable checkout. The checker audits native YAML, committed
artifact bytes and every managed post-seed Git author, even a human edit later
restored by an agent; it does not trust the index or agent-returned outcome IDs.
It proves declared-actor consistency, not authenticated identity or absence of
physical human assistance. Its success cannot complete Gate 2 by itself.

## Common options

Every invocation accepts `--project PATH` (default discover project in current
or ancestor directory), `--json` (default human table), `--read-only` (default
false), `--as-agent NAME[:MODEL[:VERSION]]` (default Git human), `--role ROLE`
(default none). Graph operations and streams additionally accept `--now TIMESTAMP`
(default current UTC rounded to whole seconds), and `--commit` / `--no-commit`
(default commit). They work before or after the
command; command-local values override group values. `init` and `import` also
accept destination positional paths and otherwise use project/current directory.
MCP requires explicit agent or human launch; it does not use the default-human
assumption. Set evaluation time and commit choice on tool calls, not the launcher.
HTTP retains the Git-human default; set evaluation time and write commit choice
on requests, not its launcher. Server output is its protocol or launch URL,
not the graph commands' JSON/table format.

All commands accept `--help`; top-level `--version` prints the package version.
Reads additionally accept `--as-of TIME|COMMIT` (default current). Commit means
committer time. Common write controls are accepted uniformly by Click, but only
writes commit graph changes; a read-only export may write its external archive.

JSON structured fields/usage must be objects; repeatable edge and artifact options
accumulate. Optional strings default absent unless stated otherwise. Optional
lists default empty. Invocation descriptions below are fragments, not complete
scripts: use your installed console, real IDs and appropriate common options.

For one-shot operations, exit 0 means success. Domain exit 2 emits one JSON object on stderr with `rule`,
`message`, and array `ids`; stdout is empty. Click usage exit 2 emits plain-text
Usage/Error on stderr, not JSON. Missing/invalid project exits 3. Do not infer an
error type solely from exit 2. Shared write errors are documented after the reference.
Server tool errors instead stay in the protocol and do not terminate the process;
an ongoing stream may already have emitted valid lines before a later error.

## CLI reference

### init
**Invocation**: `rg init [PATH]`.
**Arguments**: Optional destination; defaults to project option or current directory.
**Options and defaults**: `--profile research`; [Common options](#common-options).
**Permissions / preconditions**: Human; Git identity must be available from an existing parent repository when the destination does not yet exist. Prefer configuring the demo repository first.
**Effect / result**: Initialize layout, pin profile, commit; JSON `{path, profile, version}`.
**Errors**: Shared write rules; `already_initialised`, `unknown_profile`.

### propose
**Invocation**: `rg propose TYPE TITLE`.
**Arguments**: Type and title required.
**Options and defaults**: `--body` empty string; `--fields` absent JSON object; repeatable `--edge` empty (JSON `{type,to|from,fields?}`); [Common options](#common-options).
**Permissions / preconditions**: Human or agent; profile must define type and relations.
**Effect / result**: New node mapping, active without a gate or proposed with one; incident edges created atomically.
**Errors**: Shared write rules; `unknown_type`, `reserved_edge_type`, `schema`, `edge_endpoints`, `cycle`, `duplicate_edge`.

### link
**Invocation**: `rg link TYPE FROM TO`.
**Arguments**: Edge type and existing endpoint IDs.
**Options and defaults**: `--fields` absent JSON object; [Common options](#common-options).
**Permissions / preconditions**: Human or agent, compatible endpoint types; retired endpoints are not categorically forbidden.
**Effect / result**: New edge mapping with from/to IDs.
**Errors**: Shared write rules; `reserved_edge_type`, `edge_endpoints`, `schema`, `cycle`, `duplicate_edge`.

### approve
**Invocation**: `rg approve NODE`.
**Arguments**: Proposed node ID.
**Options and defaults**: `--what activate` (only supported value), `--note` absent; [Common options](#common-options).
**Permissions / preconditions**: Gate-compatible actor; bundled research gates human only, any human, not reviewer-only.
**Effect / result**: Node mapping with approval record; activates when gate satisfied.
**Errors**: Shared write rules; `wrong_role`, `not_proposed`, `already_approved`, `unknown_what`; research Claim `claim_needs_evidence`. Agent rejection follows not_proposed checking.

### reject
**Invocation**: `rg reject NODE REASON`.
**Arguments**: Proposed ID and reason required.
**Options and defaults**: No command-specific options; [Common options](#common-options).
**Permissions / preconditions**: Human; proposed status.
**Effect / result**: Retired node mapping; live incident edges retired, reason recorded.
**Errors**: Shared write rules; `not_proposed`.

### supersede
**Invocation**: `rg supersede NODE`.
**Arguments**: Old ID required.
**Options and defaults**: `--title`, `--body`, `--fields` absent retain old values; `--inherit-edges` / `--no-inherit-edges` defaults inherit; `--instruction` absent; [Common options](#common-options).
**Permissions / preconditions**: Human any type; agent Task/Result only; not retired or running (completed/failed can be replaced).
**Effect / result**: Replacement node mapping; old retired, supersedes relation recorded; transferable edges retired and optionally recreated; optional Instruction.
**Errors**: Shared write rules; `agent_cannot_supersede_type`, `terminal_status`, `running`, `cycle`, `schema`.

### instruct
**Invocation**: `rg instruct NODE TEXT`.
**Arguments**: Target ID and instruction text.
**Options and defaults**: No command-specific options; [Common options](#common-options).
**Permissions / preconditions**: Human; target proposed or active.
**Effect / result**: Instruction mapping with instructs relation to target; does not execute text.
**Errors**: Shared write rules; `already_ran`.

### claim
**Invocation**: `rg claim NODE`.
**Arguments**: Task ID.
**Options and defaults**: `--lease-seconds 7200`, integer at least 1; [Common options](#common-options).
**Permissions / preconditions**: Human or agent; ready claimable node, or expired running lease eligible for reclaim.
**Effect / result**: Running node mapping, new actor/acquired_at/expires_at lease and claim record.
**Errors**: Shared write rules; `lease_held` checked before `not_ready`; `not_ready`, `negative`.

### renew
**Invocation**: `rg renew NODE`.
**Arguments**: Running ID.
**Options and defaults**: `--lease-seconds 7200`, integer at least 1; [Common options](#common-options).
**Permissions / preconditions**: Lease holder only, even after expiry.
**Effect / result**: Node mapping; expiry becomes max(old expiry, now plus duration), renewal recorded.
**Errors**: Shared write rules; `not_running`, `not_lease_holder`, `negative`.

### release
**Invocation**: `rg release NODE`.
**Arguments**: Running ID.
**Options and defaults**: `--note` absent; `--usage` absent JSON object; [Common options](#common-options).
**Permissions / preconditions**: Lease holder or any human.
**Effect / result**: Active node mapping, cleared lease, added usage, release record.
**Errors**: Shared write rules; `not_running`, `not_lease_holder`, `negative`.

### complete
**Invocation**: `rg complete NODE SUMMARY`.
**Arguments**: Running ID and summary.
**Options and defaults**: Repeatable `--artifact` empty; `--usage` absent JSON object; [Common options](#common-options).
**Permissions / preconditions**: Lease holder; files must already exist under artifacts/NODE/ relative to project.
**Effect / result**: Done node mapping, cleared lease, added usage and hashed artifact records; files staged.
**Errors**: Shared write rules; `not_running`, `not_lease_holder`, `artifact_path`, `artifact_missing`, `negative`.

### fail
**Invocation**: `rg fail NODE SUMMARY`.
**Arguments**: Running ID and summary.
**Options and defaults**: `--step`, `--check`, `--measured`, `--error` absent strings; `--usage` absent JSON object; [Common options](#common-options).
**Permissions / preconditions**: Lease holder.
**Effect / result**: Failed Task mapping, cleared lease, added usage; Failure node explains target.
**Errors**: Shared write rules; `not_running`, `not_lease_holder`, `negative`.

### expand
**Invocation**: `rg expand [TARGETS...]`.
**Arguments**: One or more target IDs (parser allows empty, domain rejects).
**Options and defaults**: `--hint` absent; [Common options](#common-options).
**Permissions / preconditions**: Human; profile supports generated Expansion Task.
**Effect / result**: One Task mapping with expansion metadata; core never executes expansion hooks.
**Errors**: Shared write rules; `no_targets`.

### budget
**Invocation**: `rg budget NODE`.
**Arguments**: Target ID.
**Options and defaults**: `--tokens`, `--compute-seconds` absent integers; [Common options](#common-options).
**Permissions / preconditions**: Human; dimensions nonnegative.
**Effect / result**: Node mapping with exactly supplied dimensions (omitted dimensions removed); zero means stop, no metering.
**Errors**: Shared write rules; `negative`.

### retire
**Invocation**: `rg retire NODE REASON`.
**Arguments**: ID and reason.
**Options and defaults**: No command-specific options; [Common options](#common-options).
**Permissions / preconditions**: Human; proposed, active or failed, not running.
**Effect / result**: Retired node mapping and retired live incident edges, no replacement.
**Errors**: Shared write rules; `terminal_status`, `running`.

### attach
**Invocation**: `rg attach NODE`.
**Arguments**: Artifact owner ID.
**Options and defaults**: Repeatable `--artifact` empty; [Common options](#common-options).
**Permissions / preconditions**: Human or agent; non-retired node, existing files under artifacts/NODE/.
**Effect / result**: Node mapping with appended artifact records, existing bytes staged; no file creation/move.
**Errors**: Shared write rules; `terminal_status` (retired), `artifact_path` before `artifact_missing`.

### tag
**Invocation**: `rg tag NODE`.
**Arguments**: Target ID.
**Options and defaults**: Repeatable `--add`, `--remove` empty; `--priority` absent keeps existing, JSON integer sets, JSON null clears; [Common options](#common-options).
**Permissions / preconditions**: Human or agent.
**Effect / result**: Updated tags/priority node mapping; no approval record.
**Errors**: Shared write rules; malformed priority JSON/type gives `schema`.

### import
**Invocation**: `rg import ARCHIVE [PATH]`.
**Arguments**: Archive required; destination defaults to project option or current directory.
**Options and defaults**: No command-specific options; [Common options](#common-options).
**Permissions / preconditions**: Human; empty/nonexistent destination; now at least all restored timestamps, including future lease expiry and history.
**Effect / result**: Validate before destination creation; restore pinned layout, preserve timestamps, make one import commit; JSON `{path,profile,version}`.
**Errors**: Shared write rules including `clock_skew`; `not_empty`, `bad_archive`, `profile_too_new`.

### mcp
**Invocation**: `rg --project PATH --as-agent NAME mcp` or `rg --project PATH mcp --as-human`.
**Arguments**: None; project selection is fixed at launch.
**Options and defaults**: `--as-human` false; `--preset essential|full` (default `full`); [Common options](#common-options) for project, declared identity, role, read-only, JSON errors and help. Full registry tools are the default.
**Permissions / preconditions**: Exactly one explicit agent identity or human mode. Install the optional mcp extra; launcher help does not require it. Current MCP discovery negotiation is required.
**Effect / result**: Official SDK JSON-RPC stdio, never a graph JSON value or human table. One tool per graph registry operation with readOnlyHint; read-only hides and refuses every write including init/import. JSON text and structuredContent preserve the same data, arrays and omission/null semantics; domain errors set isError with rule/message/ids. Tool calls may set now and writes may set commit; neither can override authority. Optional init/import destinations must equal the selected root. Per-request owners close even on cancellation; disconnect shuts down the server. Profile guidance only, no core prompts, runner or scheduler.
**Errors**: `schema` for missing/conflicting launch identity, missing optional extra, authority/path overrides or unsupported negotiation; shared domain errors for tool calls. Missing/invalid project and profile errors retain the core refusal rather than being masked.

### serve
**Invocation**: `rg --project PATH serve --no-open`.
**Arguments**: None; project and declared actor are fixed at launch.
**Options and defaults**: `--host 127.0.0.1` (loopback only), `--port 0` (ephemeral), `--open/--no-open` (default opens); [Common options](#common-options) for project, identity, role, read-only, JSON errors and help. No launcher evaluation clock or commit control.
**Permissions / preconditions**: Install optional viewer extra; help remains available without it. Default serving Git human or explicit launch-fixed agent. Cookie and Host protect every API/SSE/download; exact Origin also protects exchange and writes.
**Effect / result**: Prints one fragment-credential URL and serves HTTP/SSE until shutdown. One-use exchange grants host-only HttpOnly SameSite Strict session cookie. Reads GET `/api/NAME`, writes POST `/api/NAME`, registry GET `/api/operations`. Ordinary graph JSON is unchanged; roles are per request. Live graph cursor header belongs to that snapshot; `/api/events` uses full Last-Event-ID and 15-second keepalive comments, reset means refetch. Only registered node-owned regular artifacts download as inert attachments. No-store/no-referrer responses; no unauthenticated graph, query credentials, cross-origin access or request authority overrides. Shutdown releases its serialized Project and observer.
**Errors**: `schema` for invalid launch options or missing extra; HTTP 403 for authentication/Host/Origin/authority refusal without disclosure, 422 for rule/message/ids domain errors, 503 for unavailable projects, 404 for unsafe/unregistered downloads. Cancellation is not an automatic transaction rollback or mutation retry.

### events
**Invocation**: `rg events` or `rg events --follow`.
**Arguments**: None. This is a stream, outside the 37 graph operations.
**Options and defaults**: `--follow` false; `--since` absent full cursor; `--poll-interval` 1 second, positive and finite; [Common options](#common-options). Current observations only.
**Permissions / preconditions**: Any actor; always read-only, without the writer lock. Keep stdin open to follow; closing stdin or SIGINT exits zero and closes the owner.
**Effect / result**: One notification per JSONL line under `--json`, otherwise one human line. Initial observation reports index counts; subsequent polls coalesce persisted node/edge changes and notify index/clock changes. Carry the full `v1:<generation>:<sequence>` cursor; unknown, malformed, expired or previous-instance cursors yield one index reset, requiring fresh graph/digest/ready/blocked/running/profile reads. Every invocation starts a new generation, so a cursor from an earlier invocation resets rather than pretending durable replay. Python `ChangeSource.snapshot` associates a graph with a cursor for gap-free catch-up within one source. No event log file or historical audit is created.
**Errors**: Shared read errors; `schema` for nonfinite intervals, Click usage error for nonpositive intervals.

### graph
**Invocation**: `rg graph`.
**Arguments**: None.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any actor, including read-only project.
**Effect / result**: `{nodes: [...], edges: [...]}` including retired records, each ordered by created then id. Uses the same partial historical projection as show, without its incident relations or derived flags. Reads leave graph and Git unchanged and are identical after index deletion.
**Errors**: Shared read errors; `schema` for invalid clock/as-of selectors.

### profile
**Invocation**: `rg profile`.
**Arguments**: None. Python calls `Project.profile_info`, since `Project.profile` is the loaded object.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any actor, including read-only project.
**Effect / result**: Pinned `name`/`version`, loaded `installed_version`, ordered `node_types` (name/prefix/claimable/schema), `edge_types` (name/allowed/inherit/schema), `gates` (transition/node_type/roles/agents_allowed/when_fields), `roles`, and `agent_guidance`. Includes unused declarations, excludes validators/hooks/renderers/migrations and all callable-bearing attributes. Under as-of, declarations still describe the currently loaded profile, not historical profile versions.
**Errors**: Shared read errors; `schema` for invalid clock/as-of selectors.

### show
**Invocation**: `rg show ID`.
**Arguments**: Node or edge ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor; ID visible at selected time.
**Effect / result**: Mapping; nodes add edges, instructions, pins, flags (blocked/ready/stale/lease_expired/over_budget), warnings.
**Errors**: Shared read errors; `not_found`.

### ready
**Invocation**: `rg ready`.
**Arguments**: None.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor.
**Effect / result**: `{items:[...]}` ready claimable nodes in ready order: explicit priorities first, highest first (missing last), then creation time and ID.
**Errors**: Shared read errors.

### blocked
**Invocation**: `rg blocked`.
**Arguments**: None.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor.
**Effect / result**: `{items:[...]}` blocked nodes with blocking IDs, in ready order.
**Errors**: Shared read errors.

### queue
**Invocation**: `rg queue`.
**Arguments**: None.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor.
**Effect / result**: `{items:[...]}` proposed gated nodes, gate roles and approvals, oldest first.
**Errors**: Shared read errors.

### history
**Invocation**: `rg history ID`.
**Arguments**: Node ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing node.
**Effect / result**: Oldest-first list of supersedes, approvals, artifacts and status intervals across replacement chain, with introduction commits (including retired supersedes).
**Errors**: Shared read errors; `not_found`.

### evidence
**Invocation**: `rg evidence CLAIM`.
**Arguments**: Starting Claim ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing start node.
**Effect / result**: `{items:[...]}` live cites/derived-from transitive closure with artifacts, excluding start itself.
**Errors**: Shared read errors; `not_found`.

### stale
**Invocation**: `rg stale`.
**Arguments**: None.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor.
**Effect / result**: `{items:[...]}` stale Claims once each, created then ID order; responsible is always a sorted deduplicated array of replacement-head IDs.
**Errors**: Shared read errors.

### weakest
**Invocation**: `rg weakest CLAIM`.
**Arguments**: Claim ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing start node.
**Effect / result**: List of cites targets sorted by (no superseded input, runs or zero, held_out or false, ID); superseded-input evidence first. Not scientific scoring.
**Errors**: Shared read errors; `not_found`.

### predictions
**Invocation**: `rg predictions HYPOTHESIS`.
**Arguments**: Hypothesis ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing hypothesis.
**Effect / result**: List of incoming live tests with source, expected, discriminating and source status, oldest first.
**Errors**: Shared read errors; `not_found`.

### matrix
**Invocation**: `rg matrix QUESTION`.
**Arguments**: Question ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing question.
**Effect / result**: `{rows:[{source,cells,non_discriminating}],columns:[...]}` for tests of the question's hypothesis group; cells map hypothesis ID to expected or empty, both axes creation order.
**Errors**: Shared read errors; `not_found`.

### alternatives
**Invocation**: `rg alternatives HYPOTHESIS`.
**Arguments**: Hypothesis ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing hypothesis.
**Effect / result**: List of other hypotheses answering its Questions, statuses and live supporting/contradicting Claims, creation order.
**Errors**: Shared read errors; `not_found`.

### since
**Invocation**: `rg since TIME`.
**Arguments**: Timestamp, strictly-after comparison.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor; valid whole-second UTC timestamp.
**Effect / result**: List of nodes changed by creation/update/approval/artifact and edges created/retired after time.
**Errors**: Shared read errors; invalid timestamp `schema`.

### where
**Invocation**: `rg where`.
**Arguments**: None.
**Options and defaults**: `--type`, `--status`, `--author`, `--tag`, `--text` absent; repeatable `--field K=V` empty; `--over-budget` false; `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor. Filters conjoin; field string equality splits at first equals; identical repeated fields allowed, conflicting repeats rejected.
**Effect / result**: Node list, created order; text matches title/body case-insensitively.
**Errors**: Shared read errors; invalid filters `schema`.

### notes
**Invocation**: `rg notes ID`.
**Arguments**: Target ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing target.
**Effect / result**: List of Notes with live about edges to target, newest first.
**Errors**: Shared read errors; `not_found`.

### subtree
**Invocation**: `rg subtree ID`.
**Arguments**: Root ID.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor, existing root.
**Effect / result**: `{nodes:[...],status_counts:{...},usage:{...}}`; includes the root and descendants reached backwards over live `part-of`, `tests`, `addresses` and `derived-from` edges. Usage follows those relations including retired edges, deduplicating nodes reached through multiple paths.
**Errors**: Shared read errors; `not_found`.

### digest
**Invocation**: `rg digest`.
**Arguments**: None.
**Options and defaults**: `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any declared actor.
**Effect / result**: Items envelope containing counts/lists for running, ready, blocked, awaiting, failed_24h and non-retired Claims; failed window relative to now.
**Errors**: Shared read errors.

### export
**Invocation**: `rg export OUT`.
**Arguments**: Output tar.zst path.
**Options and defaults**: `--include-artifacts` false; `--since` absent timestamp; `--as-of` current; [Common options](#common-options).
**Permissions / preconditions**: Any actor, including read-only project; writable external archive location.
**Effect / result**: Metadata `{path}`; archive contains graph, pinned profile, status histories and optional artifacts. Incremental selection includes changed records, endpoints and supersedes/cites/derived-from closure.
**Errors**: Shared read errors; `schema` for invalid timestamps/paths, `write_failed` for archive output failure.

## Troubleshooting and operational limits

Shared write rules, checked before operation-specific rules where applicable:
`read_only` means reopen writable; `clock_skew` means now precedes recorded Git
history (use an explicit later UTC time); `not_found` means check returned IDs and
selected historical visibility; `profile_too_new` means install an implementation
supporting the pinned version, not silently migrate; `human_only_approval` means
use a declared human only after real review; `wrong_role` means the declared role
does not satisfy the profile; `unsupported_by_profile` means choose a supported
operation/profile; `write_failed` means inspect files and HEAD before retrying.
Shared reads can report `project_not_found`, `invalid_project`, `profile_too_new`,
`schema` for malformed clock/as-of/filter input, or `not_found` for missing IDs.
Profile validators can add rules, such as claim_needs_evidence and pin_artifact_exists.

Git commits require local user.name/user.email. If Git reports unknown identity,
configure those in the demo/project repository, not globally. A lease_held error
requires waiting for expiry or release; expiry alone does not complete work.
Reclaim with claim at/after expiry, using the returned lease actor for completion.
Renew never shortens expiry and only the holder can renew, even after expiry.
Deleting `.researchgraph/index.sqlite` is safe: files and Git are authoritative.

No-commit is a real working-tree write, not a dry run. It changes later reads but
not HEAD and emits no event hook. Normal commits stage only the operation's files.
Validation failures leave source unchanged; write_failed after commit or an event
hook failure can leave a durable commit, so blind retries are unsafe.

Historical reads are partial: as-of reconstructs visibility/status/approvals/artifacts,
not budget, usage, tags, priority or lease; those remain current. Equal-clock commits
share time. Historical exports select membership at that time but include current
working-tree file/artifact bytes and selected nodes' full histories, including later
events. They are not redaction or a Git checkout. Query an imported archive as-of
for the historical view. Approval/artifact/supersedes introduction provenance becomes
the import commit; preserved interval commit IDs retain recorded provenance. Import
requires now at least every timestamp, including future leases; equality works,
earlier times fail before creating destination files. Nothing shifts clocks for you.

There is no authentication, sandbox, core runner/scheduler/agent prompt, metering,
discipline-specific core behavior, expansion-hook execution or concurrent-write
coordination across clones. The reference runner executes shell commands as your OS
user, supports tasks only and is unsupported. Viewer, MCP, profile renderers and
profile migrations are not delivered v0.1 integration capabilities.
