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
often means ripgrep. Set `RG` to that absolute path and run the following Python
parts using `.venv/bin/python` from a new, empty working directory **outside the
checkout**. Parts share files, not Python globals. Provisioning can download build
and package dependencies; the following workflows require no network. Git identity
is configured in each demo only, never globally. The assertions and printed JSON
are expected results, not precomputed IDs.

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

<!-- rg-example: user-lifecycle; part=1; runnable -->
```python
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

root = Path("research-demo").resolve()
root.mkdir()
for args in (("init",), ("config", "user.name", "Tutorial human"),
             ("config", "user.email", "human@example.test"),
             ("config", "commit.gpgsign", "false")):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
console = os.environ["RG"]
clock = datetime(2026, 10, 20, tzinfo=UTC)

def call(*args):
    global clock
    clock += timedelta(seconds=2)
    result = subprocess.run([console, "--project", str(root), "--json", "--now",
                             clock.isoformat(), *args], check=True,
                            capture_output=True, text=True)
    return json.loads(result.stdout)

assert call("init")["profile"] == "research"
question = call("propose", "Question", "Does adding two shift the mean?")
hypothesis = call("propose", "Hypothesis", "The shift is two")
call("link", "answers", hypothesis["id"], question["id"])
assert call("approve", hypothesis["id"])["status"] == "active"
prediction = call("propose", "Prediction", "Mean difference is two")
call("link", "tests", prediction["id"], hypothesis["id"], "--fields",
     json.dumps({"expected": "paired mean difference = 2"}))
assert call("approve", prediction["id"])["status"] == "active"
task = call("propose", "Task", "Measure fixed paired data")
call("link", "addresses", task["id"], prediction["id"])
assert task["id"] in {item["id"] for item in call("ready")["items"]}
Path("user-state.json").write_text(json.dumps({"project": str(root),
    "hypothesis": hypothesis["id"], "task": task["id"], "clock": clock.isoformat()}))
```

Now a distinct declared agent claims and completes exactly one Task. Save the
Result's bytes separately and register them on the Result. Review those bytes and
the cites relation before approving the Claim. A Claim becomes active, not done;
the validator verifies a live cites edge, not the truth of its content. Finally
replace a separate follow-up Task and inspect its preserved history. No scheduler is
hidden in these steps.

<!-- rg-example: user-lifecycle; part=2; runnable -->
```python
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

state = json.loads(Path("user-state.json").read_text())
root = Path(state["project"])
clock = datetime.fromisoformat(state["clock"])

def call(*args, agent=False):
    global clock
    clock += timedelta(seconds=2)
    prefix = [os.environ["RG"], "--project", str(root), "--json", "--now", clock.isoformat()]
    if agent:
        prefix += ["--as-agent", "tutorial-worker"]
    result = subprocess.run([*prefix, *args], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)

task = state["task"]
assert call("claim", task, agent=True)["status"] == "running"
relative = "artifacts/" + task + "/paired.json"
file = root / relative
file.parent.mkdir(parents=True)
file.write_text(json.dumps({"baseline": [1, 2, 3], "shifted": [3, 4, 5], "delta": 2}))
assert call("complete", task, "Paired shift measured", "--artifact", relative,
            "--usage", '{"tokens": 0}', agent=True)["status"] == "done"
result = call("propose", "Result", "Measured paired shift", "--fields", '{"runs": 1}')
call("link", "derived-from", result["id"], task)
result_relative = "artifacts/" + result["id"] + "/paired.json"
saved = root / result_relative
saved.parent.mkdir(parents=True)
saved.write_bytes(file.read_bytes())
assert call("attach", result["id"], "--artifact", result_relative)["artifacts"]
claim = call("propose", "Claim", "This constructed sample has shift two")
call("link", "cites", claim["id"], result["id"])
call("link", "supports", claim["id"], state["hypothesis"])
assert call("approve", claim["id"])["status"] == "active"
assert any(item["id"] == result["id"] and item["artifacts"]
           for item in call("evidence", claim["id"])["items"])
assert call("show", task)["lease"] is None
followup = call("propose", "Task", "Plan a follow-up")
replacement = call("supersede", followup["id"], "--title", "Repeat with a larger sample")
assert replacement["id"] != followup["id"]
assert call("history", followup["id"])
print(json.dumps({"project": str(root), "claim": claim["id"], "replacement": replacement["id"]}))
```

## Common options

Every invocation accepts `--project PATH` (default discover project in current
or ancestor directory), `--json` (default human table), `--read-only` (default
false), `--as-agent NAME[:MODEL[:VERSION]]` (default Git human), `--role ROLE`
(default none), `--now TIMESTAMP` (default current UTC rounded to whole seconds),
and `--commit` / `--no-commit` (default commit). They work before or after the
command; command-local values override group values. `init` and `import` also
accept destination positional paths and otherwise use project/current directory.

All commands accept `--help`; top-level `--version` prints the package version.
Reads additionally accept `--as-of TIME|COMMIT` (default current). Commit means
committer time. Common write controls are accepted uniformly by Click, but only
writes commit graph changes; a read-only export may write its external archive.

JSON structured fields/usage must be objects; repeatable edge and artifact options
accumulate. Optional strings default absent unless stated otherwise. Optional
lists default empty. Invocation descriptions below are fragments, not complete
scripts: use your installed console, real IDs and appropriate common options.

Exit 0 means success. Domain exit 2 emits one JSON object on stderr with `rule`,
`message`, and array `ids`; stdout is empty. Click usage exit 2 emits plain-text
Usage/Error on stderr, not JSON. Missing/invalid project exits 3. Do not infer an
error type solely from exit 2. Shared write errors are documented after the reference.

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
