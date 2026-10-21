# researchgraph integration manual

For external Python/CLI integrations with researchgraph 0.0.1, the v0.1 milestone,
and bundled profiles research 1.0 and tasks 1.0. This is descriptive documentation,
not a replacement for the [normative operations](../spec/operations.md). Report
implementation/specification disagreements rather than editing the spec to satisfy
these examples. The [user manual](user-manual.md) supplies the complete 37-graph-command
reference, the events stream, optional MCP/HTTP servers and everyday concepts; the [research tutorial](research-tutorial.ipynb)
is a synthetic experiment with explicitly simulated human approvals.

## Installation from source or a local wheel

Prerequisites: Python 3.11+, Git, C compiler and uv. CPython 3.14.4, CPython 3.11
and PyPy 3.11 functionality have CI gates, not a claim of universal platform,
free-threaded or stable-ABI support. Two native extensions must compile for the
selected interpreter. Use a fresh checkout, not a published-index package name.
[Source installation](user-manual.md#prerequisites-and-installation) is a complete
executable recipe. For a distributable local wheel use this alternative from the
checkout root; choose a fresh checkout/environment rather than overwriting an
unrelated existing environment. Provisioning can download build/runtime dependencies.

<!-- rg-example: wheel-install; part=1; runnable -->
```sh
uv build --python "${UV_PYTHON:-python3}" --wheel --out-dir dist
uv venv --python "${UV_PYTHON:-python3}" .venv
uv pip install --python .venv/bin/python dist/*.whl "rpds-py==0.25.1; platform_python_implementation == 'PyPy' and python_version == '3.11'"
.venv/bin/rg --version
```

Use the absolute installed `.venv/bin/rg`, never PATH's possibly-ripgrep `rg`.
The native wheel is interpreter/platform-specific. The PyPy dependency constraint
selects an existing jsonschema dependency version; it adds no core dependency.

## Public API and resource ownership

Import Project, Actor, RuleViolation and __version__ from researchgraph. Create
with Project.init(path, profile='research', actor=..., now=...), reopen with
Project.open(path, read_only=False), or restore with
Project.import_(archive, path, actor=..., now=...). These own index connections and
helper processes: use a context manager, including on exceptions, or call close()
in finally. Do not retain an open Project indefinitely or copy its internal state.
The package's __version__ is distinct from the pinned profile version and milestone.

Named writes are init, propose, link, approve, reject, supersede, instruct, claim,
renew, release, complete, fail, expand, budget, retire, attach, tag and import_.
Named reads are show, ready, blocked, queue, history, evidence, stale, weakest,
predictions, matrix, alternatives, since, where, notes, subtree, digest and export.
CLI import is Python import_; from is from_ where Python requires it. Most named
operations return serialized dictionaries, not mutable model objects: writing into
a returned dictionary does not update the graph. See the command reference for
arguments, permissions and domain rules; help(Project) provides API signatures.
Project.apply is the implementation's low-level Write boundary, with no CLI or MCP
door, not a frozen integration API; integrations should use the named operations.

Every named write takes keyword actor, explicit now (whole-second aware UTC),
optional role=None and commit=True. Named reads require actor and now, accept
role=None and as_of=None, not commit. Constructors have their own ownership/path
arguments; open does not record an actor/time. The CLI defaults now to wall clock;
the Python API intentionally does not. Advance now between writes when history
must distinguish events; now must not precede the repository clock. Do not assume
IDs, commit hashes or wall-clock timestamps in tests. Default leases last 7200s;
renew extends to max(existing, now+duration). Complete/fail require the lease
holder; release allows a human override. Only Tasks are claimable in both bundled
profiles. Research Hypothesis/Prediction/Claim gates admit any human; declaring
reviewer is not required and agents cannot substitute for human approval.

get_node(id), get_nodes(), get_edges() are public **current** low-level reads,
returning researchgraph.model Node/Edge records, including retired records. They
accept no actor, now or as_of. Use named queries for historical or derived views.
Model records encountered by consumers include Actor (kind/name plus optional
email/model/version), Node (id/type/title/body/author/created, fields/status and
optional lease plus approvals/artifacts/budget/usage/tags/priority), Edge
(id/type/from_/to/author/created/fields/retired), Approval (actor/at/what, optional
role/note), Artifact (path/sha256/bytes/added_at/added_by), Lease
(actor/acquired_at/expires_at), and Status. Model to_dict/from_dict handles wire
serialization; enum status becomes a string and timestamps become UTC strings.
Absent optional values may be null, lists/maps can be empty. Wire edge keys are
from/to, not Python's from_. Do not serialize via object __dict__.

## Observed changes and snapshot catch-up

`Project.changes(actor=..., now=..., role=None, since=None)` owns a bounded
in-memory observer until that Project closes. It returns `events` and the latest
opaque `cursor`. Without `since`, only this observation's envelopes are returned;
a retained cursor returns strictly its successors. This is a current-state read,
not a historical query, durable event log, scheduler or write-success hook.

For a snapshot/subscription boundary, import `ChangeSource` from
`researchgraph.events`, borrow an open Project with `ChangeSource(project,
retention=1024)`, and call `source.snapshot(actor=..., now=...)`. Its `graph` and
`cursor` belong to the same observation. Catch up with
`source.changes(actor=..., now=..., since=snapshot_cursor)`. Serialize calls to a
source. Close the source to discard its retained state, and close its borrowed
Project when the consumer disconnects; source.close does not close that owner.

Fold complete `node` and `edge` records by id; `index` counts include retired
records. A `reset: true` index requires a fresh associated snapshot and refreshed
digest/ready/blocked/running/profile reads. Resets cover expired, malformed,
unknown/future and previous-instance cursors, and disappeared records. Carry the
entire `v1:<random-generation>:<sequence>` token unchanged. Restart always mints a
new generation, even when source bytes and sequence numbers overlap.
Cursor-recovery resets reuse the current retained index's cursor and observation
time without appending or evicting other consumers, even at retention one. Actual
record disappearance still emits a new shared reset (decision 024).

Observations detect external/no-commit/same-second writes, retained bytes after
write failures, HEAD/raw-source changes, lease expiry and failure-window changes.
Clock-only notifications never rewrite stored statuses. Cache rebuilds and fully
rolled-back/refused writes fabricate no domain events. Polling can coalesce
intermediate states; envelope time and HEAD describe observation, not causation.

## Optional MCP contract

Install the `mcp` extra for the official SDK; core import and launcher help do
not import it. `rg mcp` requires explicit agent or human mode, fixes project,
actor, role and read-only, and runs only SDK stdio framing. Initialization uses
current discovery negotiation (decision 021). Legacy initialize-only tool calls
are explicitly refused: their wire cannot carry the required bare array results.
SDK/memory success is not proof of actual agent-product compatibility.

`researchgraph.mcp.create_server(root, actor=..., role=None, read_only=False, preset="full")`
returns the official low-level Server for an existing SDK transport;
`serve` runs its stdio transport. Full tools equal the graph registry, not streams
or launchers; readOnlyHint follows kind. Read-only hides and refuses every write,
including init/import. Instructions are profile guidance only, empty before an
uninitialized selected destination bootstraps. Invalid projects are not masked.
Essential is the sixteen-tool subset in `spec/operations.md`; excluded operations
refuse rather than silently reaching the full dispatcher. Read-only write
refusals retain the shared permission preflight even outside that subset.

Input schemas contain registry arguments plus optional now and write commit.
Authority/output options are never tool arguments; initializer paths cannot
change the selected destination. Omitted scalar/object values stay omitted;
empty repeated collectors become empty arrays as in Click. The shared interface
dispatcher calls public Project methods without SQL or duplicate business rules.
JSON text and structuredContent contain the identical value, including arrays;
RuleViolation sets isError and rule/message/ids. No result envelope is invented.

Each call owns and closes its Project in a worker thread. Cancellation waits for
that work's cleanup rather than abandoning the owner. It is not a rollback
promise: a cancelled write can still commit. Official stdio stream ownership
keeps even callback stdout out of protocol framing; diagnostic output belongs
on stderr. Real installed transport and isolated core/extra wheel consumers are
tested separately from memory-client domain scenarios.

## Optional HTTP/SSE contract

Install `researchgraph[viewer]` for Starlette/Uvicorn; neither belongs in the core
dependencies or launcher-help imports. `rg serve` fixes root/actor/read-only at
launch, human Git identity by default or explicit `--as-agent`. Role is declared
per request. Loopback only, ephemeral port by default; `--no-open` suppresses the
browser but still prints the private one-use fragment URL. The static host strips
that fragment before the JSON session exchange. No query credentials or access logs.

`researchgraph.serve.create_app(root, actor=..., role=None, read_only=False, origin=...)`
returns the ASGI app; set origin to the actual bound HTTP loopback origin. Its
lifespan constructs and closes one Project and borrowed ChangeSource in the serving
thread. Requests and one-second polling share one async serialization condition,
not a persistent core writer lock. HTTP and MCP use the same registry preparation
and ordinary public dispatch; no SQL or copied graph validators live here.

POST `/api/session` exchanges the instance's one-use token for a host-only,
HttpOnly, SameSite Strict cookie. API/SSE/downloads require it and the matching
Host. Reads may omit Origin; exchange/writes must supply the exact Origin, and
foreign origins always fail. All responses are no-store/no-referrer. Actor/root/
read-only cannot come from request arguments. Initializer destinations must equal
the selected root; read-only write preflight precedes bad clocks or archives.

GET `/api/operations` returns the bare registry. GET `/api/NAME` reads use JSON
for structured/repeated query values (JSON strings are also accepted); POST writes
take object bodies. Optional now and write commit preserve ordinary semantics;
role is request context. Values/envelopes remain those of the public operations.
RuleViolation is 422 with rule/message/ids, project-open failure 503, authentication/
authority 403 without disclosure. Current `/api/graph` calls ChangeSource.snapshot
and advertises exactly its X-Researchgraph-Cursor; as-of reads advertise no live
cursor. `/api/events` uses the full cursor for id/Last-Event-ID, successor replay,
reset/refetch and 15-second keepalive comments. No per-client unbounded queue or
persistent log; a disconnected client catches up or resets against bounded retention.

`/artifacts/NODE/PATH` accepts only that node's registered existing regular files.
Decoded and multiply encoded traversal, symlinks and path swaps fail without path
disclosure. Directory-relative no-follow descriptor opening holds containment
through the final open, and response/disconnect cleanup closes the file. Responses
are octet-stream, attachment with encoded basename, nosniff; hostile HTML/SVG stays
inert. The wheel contains the framework-free assets in `viewer/static/`, served
under `/assets/`. Transport authentication is not a filesystem sandbox,
cryptographic actor identity or unassisted-agent proof.

After the host completes session exchange, import `/assets/research-graph.js` and
mount `<research-graph src="/api"></research-graph>`. The API prefix must be
same-origin without credentials, query or fragment. Each instance fetches profile,
graph and digest independently, owns its EventSource and abort controllers, and
releases them on disconnect or source change. `node-select` bubbles across the
shadow boundary with `{id, node}`; `reload()` requests fresh data. Index events
refetch snapshots; resets also refresh profile metadata. Failed refreshes are
explicitly stale/error states, never successful empty graphs. `layoutGraph(graph)`
returns detached nodes/edges, ranking only non-retired I1 edges with finite Kahn
placement and deterministic created/id placement for unplaced invalid-cycle nodes.
History collapse and filters are presentation-only. User data is text, shadow CSS
is an external packaged stylesheet, and the strict self-only CSP remains in force.

`/assets/inspector.js` registers `<research-inspector src="/api">`; `select(id)`
or its `node-id` attribute loads native `show` and related query results. Its
`node` and `related` getters are detached values. The host bridges graph
`node-select` to inspector selection and inspector `mutation-complete` to graph
refresh. Shared `api.js` preserves logical JSON/omitted arguments and reports
actual rule/message/ids errors; `dom.js` provides text-only element construction.
`X-Researchgraph-Read-Only` on authenticated API responses determines whether
write controls can be shown; unknown metadata fails closed. Profile roles are
request context, never actor/root/read-only overrides. One deliberate submission
issues one POST, followed by authoritative readback even on errors. Selection-read
cancellation is separate from mutation lifetime; cancellation does not promise
rollback. A retained-byte commit/hook failure remains an error while fresh history
can reveal its persisted replacement and Instruction. No mutation retry is hidden
in refresh or component reconnection.

`review-queue.js` registers `research-review-queue`: `items` is a detached native
queue result, while `reload()` refetches queue, blocked and profile. Applicable
gate roles come from the server; declared conditions are shown without a second
client gate evaluator. Cards count blocked records whose authoritative blocking
array names that node. Approval/rejection is one POST followed by readback even
after refusal. The host refreshes graph/inspector after reviews and reviews after
inspector mutations. `node-select` with `section: "evidence"` uses inspector
`openSection(name)` after selection. Snapshot freshness is explicit; this panel
does not claim a durable notification log or authenticated reviewer identity.

`events.js::subscribe(prefix, {onEvent, onState, onReconnect})` owns one EventSource
and browser online/offline listeners per caller; its idempotent `close()` releases
both. It retains full opaque cursors in memory with bounded replay deduplication.
The graph patches native node/edge records immediately, preserves presentation
state, and performs authoritative profile/graph/digest readback on reset/index or
reconnection. An in-flight older snapshot cannot overwrite a newer delta.

`dashboard.js` registers `research-dashboard`. Its detached `snapshot` contains
native graph/digest/stale results, dedicated ready/blocked/running results, native
subtree membership with disjoint progress buckets, an overall distinct-member
total, seven-day Failure-creation records and Claim relationships. On index,
reset and reconnect, monitoring explicitly reads `ready`, `blocked` and
`where(status="running")` rather than substituting digest groups. When Claim is
declared, rows come from `where(type="Claim")` and each includes the native
`evidence(claim)` result, whose transitive inputs are not just direct cites.
The progress precedence is retired, done, failed, native
ready, native blocked, running, proposed, other. Summary groups remain the native
overlapping digest groups, not invented disjoint counts. Activity expiry labels
explicitly compare recorded lease timestamps with browser wall time. Detailed
panels retain their open state; the in-memory plain-text event log is bounded to
100 received envelopes. Only successful readbacks emit `snapshot-updated`; actual
reset notifications emit `snapshot-reset` to refresh the selected inspector.
The host refreshes queue/navigation counts after monitoring readbacks. Required
real Chromium tests include clock-only changes, actual disappearance reset and
fifty live running nodes plus twenty external installed-CLI claims per cache mode,
each measured through both SSE and the actual graph/dashboard DOM under three
seconds, with all samples retained. This is not external usability proof.

## Runnable graph, events and HTTP example

Use a fresh private working directory, Python 3.11+, Git and `curl`. Set `RG` to the
absolute installed console path from an environment with `researchgraph[viewer]`.
These two Python blocks run in order in that directory. They use live, whole-second
UTC evaluation, not a fixed historical clock. The launch URL and cookie file are
private credentials: do not publish them or include them in session recordings.

<!-- rg-example: live-interfaces; part=1; runnable -->
```python
import os
import subprocess
from pathlib import Path

root = Path("live-demo").resolve()
root.mkdir()
subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
for key, value in (("user.name", "Demo Human"), ("user.email", "demo@example.invalid")):
    subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
console = [os.environ["RG"], "--project", str(root), "--json"]
subprocess.run(console + ["init", "--profile", "tasks"], check=True, capture_output=True)
subprocess.run(console + ["propose", "Task", "Observe live interfaces"], check=True, capture_output=True)
```

The next block checks `graph` and one-shot JSONL `events`, launches `serve` on an
ephemeral loopback port, exchanges its one-use fragment token, and compares an
actual authenticated `curl` graph read with the CLI. It closes the server even
on failure. `events --follow` is a separate long-running observation stream, not
a durable audit log; reset notifications require authoritative readback.

<!-- rg-example: live-interfaces; part=2; runnable -->
```python
import json
import os
import selectors
import signal
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

root = Path("live-demo").resolve()
console = [os.environ["RG"], "--project", str(root)]

def output(arguments):
    return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=15).stdout

graph = json.loads(output(console + ["--json", "graph"]))
events = [json.loads(line) for line in output(console + ["--json", "events"]).splitlines()]
assert len(graph["nodes"]) == 1 and events[0]["kind"] == "index"
server = subprocess.Popen(console + ["serve", "--no-open"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    with selectors.DefaultSelector() as ready:
        ready.register(server.stdout, selectors.EVENT_READ)
        assert ready.select(timeout=10), "Server did not print its launch URL"
    launch = urlsplit(server.stdout.readline().strip())
    assert launch.fragment.startswith("token=")
    origin = f"{launch.scheme}://{launch.netloc}"
    cookie_path = Path("session.cookies").resolve()
    cookie_path.touch(mode=0o600, exist_ok=False)
    cookie = str(cookie_path)
    curl = ["curl", "--silent", "--show-error", "--fail", "--max-time", "10"]
    output(curl + ["--cookie-jar", cookie, "--header", f"Origin: {origin}",
                   "--header", "Content-Type: application/json", "--data",
                   json.dumps({"token": launch.fragment.removeprefix("token=")}), origin + "/api/session"])
    actual = json.loads(output(curl + ["--cookie", cookie, origin + "/api/graph"]))
    assert actual == graph
finally:
    if server.poll() is None:
        server.send_signal(signal.SIGINT)
    try:
        _, errors = server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.communicate()
        raise
    assert server.returncode == 0, errors
print(json.dumps({"task": graph["nodes"][0]["id"], "graph_nodes": len(graph["nodes"]),
                  "event_kind": events[0]["kind"], "http_matches_cli": actual == graph}))
```

This verifies installed interface behavior, not Claude Code/Codex compatibility,
unassisted work or an outside researcher's usability session.

## Wrapper contracts, errors and durability

For graph operations, --json stdout is one JSON value; events instead emits
one JSON object per line. Non-JSON output is human-facing, not a stable machine
interface. T15 freezes the graph JSON families:

- Writes generally return node mappings, link an edge mapping. init/import return
  path/profile/version metadata; export returns path metadata.
- show adds edges, instructions, pins, flags and warnings to a node.
- ready/blocked/queue/stale/evidence/digest use items
  envelopes; extra fields include blocking, gate, approvals, responsible and artifacts.
  stale.responsible is always an array, never a scalar.
- weakest/predictions/alternatives/notes/since/where return plain lists, not items
  envelopes.
- history is an oldest-first list of kind (interval/approval/artifact/supersedes),
  node, commit, at and event-specific fields.
- matrix has rows with source/cells/non_discriminating and columns; subtree has
  nodes/status_counts/usage. Do not impose an invented universal wrapper.

CLI common options work before/after commands: --project, --json, --read-only,
--as-agent NAME[:MODEL[:VERSION]], --role, --now, --commit/--no-commit. Read --as-of
accepts time or commit. --fields and --usage are JSON objects; --edge is repeatable
JSON with type/to-or-from/fields; --artifact repeats paths. where --field K=V splits
at first equals and conjoins all filters: identical repeats allowed, conflicts
schema errors. Quote JSON for the shell or pass argument arrays from subprocess.

Exit 0 is success; 2 is either RuleViolation JSON on stderr (rule/message/ids)
or Click usage text on stderr; 3 is absent/invalid project. Domain stdout is empty.
Catch RuleViolation in Python and inspect rule, message, ids; do not parse its
formatted exception string. Other Python/OS errors are not promised domain errors.
The executable example below verifies actual stderr, not copied expected payloads.

--read-only rejects writes but allows exporting an external archive. --no-commit
is a durable working-tree change, not a dry run: subsequent queries see it, HEAD
does not change, and no event hook fires. Hooks fire only after commit; normal
writes stage only their files. Validation errors leave source unchanged. A
write_failed after commit or event-hook failure can leave a durable commit: inspect
files and HEAD before deciding whether to retry. Do not interpret every raised
exception as rollback. Files are authoritative and index.sqlite may be deleted.

Historical reads reconstruct visibility/status/approvals/artifacts, **not** mutable
budget/usage/tags/priority/lease. Commits mean committer timestamps and equal-clock
commits are indistinguishable by time. Historical export is selection, not redaction:
selected graph/artifact bytes are current and status histories may contain later
intervals/events. Use as_of queries on the import for a historical view. Import
into an empty/nonexistent destination requires now at least every restored time,
including complete history and future lease expiry; equality works, timestamps
are never shifted, too-early input fails before creating destination. Introduction
provenance of approvals/artifacts/supersedes becomes the import commit; status
intervals retain recorded commit IDs.

## Executable integration: API, real console, archive and errors

Set RG to the absolute installed console and use its environment's Python. Run the
two parts in order from a new empty directory outside the checkout. No network or
prepared project is used. Fixed UTC clocks, distinct declared human/agent actors,
local Git identity and returned IDs make the assertions reproducible. The human
approvals are simulated review: inspect hypotheses/predictions, artifact bytes and
cites before approving in real work; a successful automated test authenticates no one.

<!-- rg-example: integration; part=1; runnable -->
```python
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from researchgraph import Actor, Project, RuleViolation, __version__

root = Path("integration-demo").resolve()
root.mkdir()
for args in (("init",), ("config", "user.name", "Integration human"),
             ("config", "user.email", "integrator@example.test"),
             ("config", "commit.gpgsign", "false")):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
human = Actor("human", "Integration human", "integrator@example.test")
worker = Actor("agent", "integration-worker")
clock = datetime(2026, 10, 21, tzinfo=UTC)

def context(actor=human):
    global clock
    clock += timedelta(seconds=2)
    return {"actor": actor, "now": clock}

with Project.init(root, profile="research", **context()) as project:
    question = project.propose("Question", "Can we retain evidence?", **context())
    hypothesis = project.propose("Hypothesis", "Registered bytes are retrievable", **context())
    project.link("answers", hypothesis["id"], question["id"], **context())
    project.approve(hypothesis["id"], **context())
    prediction = project.propose("Prediction", "Evidence query contains saved bytes", **context())
    project.link("tests", prediction["id"], hypothesis["id"],
                 fields={"expected": "Result artifact is present"}, **context())
    project.approve(prediction["id"], **context())
    task = project.propose("Task", "Write synthetic result", **context())
    project.link("addresses", task["id"], prediction["id"], **context())
    assert task["id"] in {item["id"] for item in project.ready(**context(worker))["items"]}
    project.claim(task["id"], **context(worker))
    relative = "artifacts/" + task["id"] + "/result.json"
    output = root / relative
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps({"synthetic": True, "values": [2, 4, 6], "mean": 4}))
    done = project.complete(task["id"], "Saved synthetic data", artifacts=[relative],
                            usage={"tokens": 7}, **context(worker))
    assert done["status"] == "done" and done["lease"] is None
    result = project.propose("Result", "Saved synthetic data", **context())
    project.link("derived-from", result["id"], task["id"], **context())
    saved_relative = "artifacts/" + result["id"] + "/result.json"
    saved = root / saved_relative
    saved.parent.mkdir(parents=True)
    saved.write_bytes(output.read_bytes())
    project.attach(result["id"], [saved_relative], **context())
    claim = project.propose("Claim", "Evidence survives reopening", **context())
    try:
        project.approve(claim["id"], **context())
    except RuleViolation as error:
        assert error.rule == "claim_needs_evidence"
    else:
        raise AssertionError("Ungrounded Claim approval succeeded")
    # Leave the Claim proposed for real-console negative tests in part 2.
    assert project.get_node(task["id"]).status.value == "done"
Path("integration-state.json").write_text(json.dumps({"project": str(root),
    "claim": claim["id"], "result": result["id"], "hypothesis": hypothesis["id"],
    "clock": clock.isoformat(), "version": __version__}))
```

The next part captures distinct domain/usage channels, supplies evidence, approves,
then exports from a read-only project and imports through the API. The restore
uses commit=False so identity can be configured locally in the newly created
repository, then explicitly commits the restored files with Git; this deliberate
pending-restore path emits no API event hook. A normal committed import needs
effective Git identity already supplied by its caller's scoped environment.
The lease has
been cleared; choosing a later import clock still accounts for every retained time.
The archive includes artifact bytes; it is not a historical privacy filter.

<!-- rg-example: integration; part=2; runnable -->
```python
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from researchgraph import Actor, Project

state = json.loads(Path("integration-state.json").read_text())
root = Path(state["project"])
clock = datetime.fromisoformat(state["clock"])
console = os.environ["RG"]
errors = []

def run(*arguments, project=root):
    global clock
    clock += timedelta(seconds=2)
    return subprocess.run([console, "--project", str(project), "--json", "--now",
                           clock.isoformat(), *arguments], capture_output=True, text=True)

def success(*arguments):
    value = run(*arguments)
    assert value.returncode == 0, value.stderr
    return json.loads(value.stdout)

def rejected(case, arguments, rule, code=2, project=root):
    value = run(*arguments, project=project)
    assert value.returncode == code and value.stdout == ""
    if rule is None:
        assert "Usage:" in value.stderr and "Error:" in value.stderr
    else:
        error = json.loads(value.stderr)
        assert error["rule"] == rule and isinstance(error["ids"], list)
    errors.append({"case": case, "returncode": value.returncode,
                   "stdout": value.stdout, "stderr": value.stderr})

rejected("agent-approval", ["--as-agent", "integration-worker", "approve", state["claim"]], "human_only_approval")
rejected("claim-evidence", ["approve", state["claim"]], "claim_needs_evidence")
rejected("read-only", ["--read-only", "propose", "Task", "Forbidden"], "read_only")
rejected("missing-project", ["digest"], "project_not_found", code=3, project=Path("absent-project").resolve())
rejected("usage", ["propose"], None)
success("link", "cites", state["claim"], state["result"])
success("link", "supports", state["claim"], state["hypothesis"])
assert success("approve", state["claim"])["status"] == "active"
assert any(item["id"] == state["result"] and item["artifacts"]
           for item in success("evidence", state["claim"])["items"])
archive = Path("integration.tar.zst").resolve()
success("--read-only", "export", str(archive), "--include-artifacts")
imported = Path("imported-demo").resolve()
human = Actor("human", "Integration human", "integrator@example.test")
# A destination containing even .git is nonempty. Restore without commit first,
# then configure identity in the newly created repository before committing.
restored_at = clock + timedelta(days=1)
with Project.import_(archive, imported, actor=human, now=restored_at, commit=False) as project:
    for key, value in (("user.name", "Integration human"),
                       ("user.email", "integrator@example.test"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(imported), "config", key, value], check=True)
    subprocess.run(["git", "-C", str(imported), "add", "."], check=True)
    commit_environment = os.environ.copy()
    commit_environment.update(GIT_AUTHOR_DATE=restored_at.isoformat(),
                              GIT_COMMITTER_DATE=restored_at.isoformat())
    subprocess.run(["git", "-C", str(imported), "commit", "-m", "rg: import"],
                   env=commit_environment, check=True, capture_output=True)
    evidence = project.evidence(state["claim"], actor=human, now=clock + timedelta(days=1))
    assert any(item["id"] == state["result"] for item in evidence["items"])
    for node in project.get_nodes():
        for artifact in node.artifacts:
            data = (imported / artifact.path).read_bytes()
            assert len(data) == artifact.bytes
            assert hashlib.sha256(data).hexdigest() == artifact.sha256
print(json.dumps({"project": str(root), "imported": str(imported), "errors": errors}))
```

## Notebook setup and execution

This headless path needs no frontend, widgets, nbconvert or additional required UI
package. Start at a fresh checkout root. This setup installs the existing test
extra (including pinned nbclient, nbformat and ipykernel), not runtime additions.
Use the resulting interpreter for the next Python block. The notebook execution
acceptance gate is CPython 3.14.4; dependency installation is also checked on
CPython 3.11/PyPy 3.11, not a claim of notebook execution on PyPy.

<!-- rg-example: notebook-setup; part=1; runnable -->
```sh
uv venv --python "${UV_PYTHON:-python3}" .venv
uv pip install --python .venv/bin/python -e '.[test]' tests/testprofiles "rpds-py==0.25.1; platform_python_implementation == 'PyPy' and python_version == '3.11'"
.venv/bin/rg --version
```

Export RESEARCHGRAPH_CHECKOUT_ROOT as the absolute current checkout root. Supply
TMPDIR as an existing writable directory outside the checkout (the automated
harness supplies one); do not put the demo or temporary kernel under source.
The code validates this checkout, reads the actual notebook, uses a private
kernelspec whose argv is this interpreter, executes a fresh notebook with fatal
errors and finite timeout, and never writes source outputs. Kernel resources are
closed even when a cell fails. The notebook itself prints the absolute retained
demo path and creates exactly one researchgraph-tutorial-* parent directly under
TMPDIR, with demo inside it. Inspect that directory afterward; approval in an
automated run is simulated declared-human review, not authenticated human review.
The kernel receives a private home and IPython/Jupyter configuration directories;
inherited user startup files and Python startup overrides are not loaded.

<!-- rg-example: notebook-execution; part=1; notebook -->
```python
import json
import os
import sys
import tempfile
from pathlib import Path

import nbformat
import researchgraph
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager
from nbclient import NotebookClient

checkout = Path(os.environ["RESEARCHGRAPH_CHECKOUT_ROOT"]).resolve(strict=True)
assert (checkout / "pyproject.toml").is_file()
assert (checkout / "src/researchgraph/project.py").is_file()
assert (checkout / "docs/developer-manual.md").is_file()
assert Path(researchgraph.__file__).resolve() == checkout / "src/researchgraph/__init__.py"
source = checkout / "docs/research-tutorial.ipynb"
assert source.is_file()
assert Path.cwd().resolve() == checkout or not Path.cwd().resolve().is_relative_to(checkout)
external = Path(os.environ["TMPDIR"]).resolve(strict=True)
assert external.is_dir() and not external.is_relative_to(checkout)
assert not checkout.is_relative_to(external)
environment = {name: value for name, value in os.environ.items()
               if not name.startswith(("PYTHON", "IPYTHON", "JUPYTER_"))}
for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
             "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE",
             "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
    environment.pop(name, None)
environment.update(RESEARCHGRAPH_CHECKOUT_ROOT=str(checkout), TMPDIR=str(external),
                   GIT_CONFIG_GLOBAL=str(external / "absent-global-gitconfig"),
                   GIT_CONFIG_NOSYSTEM="1", PYTHONNOUSERSITE="1")
with tempfile.TemporaryDirectory(prefix="researchgraph-kernel-", dir=external) as temporary:
    work = Path(temporary)
    home = work / "home"
    home.mkdir()
    environment.update(HOME=str(home), IPYTHONDIR=str(home / "ipython"),
                       JUPYTER_CONFIG_DIR=str(home / "jupyter-config"),
                       JUPYTER_DATA_DIR=str(home / "jupyter-data"),
                       JUPYTER_RUNTIME_DIR=str(home / "jupyter-runtime"))
    kernels = work / "kernels"
    specification = kernels / "researchgraph-tutorial"
    specification.mkdir(parents=True)
    (specification / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "researchgraph isolated tutorial", "language": "python"}))
    specs = KernelSpecManager(kernel_dirs=[str(kernels)], ensure_native_kernel=False)
    manager = KernelManager(kernel_spec_manager=specs, kernel_name="researchgraph-tutorial")
    notebook = nbformat.read(source, as_version=4)
    client = NotebookClient(notebook, km=manager, kernel_name="researchgraph-tutorial", timeout=120,
                            allow_errors=False, resources={"metadata": {"path": str(work)}})
    try:
        client.execute(env=environment, cwd=str(work), cleanup_kc=True)
    finally:
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
        manager.cleanup_resources()
    for cell in notebook.cells:
        if cell.cell_type == "code":
            for output in cell.outputs:
                if output.output_type == "stream":
                    print(output.text, end="")
```

The notebook uses a fixed seed and standard-library synthetic paired data, saves
Result-owned artifact bytes, and verifies evidence. Its Hypothesis/Prediction/Claim
review gates are simulated with one declared human; a different agent claims and
completes the Task. It does not use the tasks reference runner, external datasets,
network, input(), widgets or a scheduling loop. Saved graph/artifact checks matter,
not merely completion of cells or saved output bytes.

## Local verification and troubleshooting

After notebook setup, the following is a command fragment for maintainers selecting
their own external basetemp; it is not another workflow recipe and does not create
or modify a project by itself.

<!-- rg-example: verification-command; part=1; fragment -->
```sh
.venv/bin/python -m pytest tests/test_docs.py --index-mode both
.venv/bin/python -m pytest tests/test_notebook.py --index-mode kept
```

CI runs docs checks on its existing interpreter gates and actual notebook execution
in CPython 3.14.4 acceptance/scale, not the compatibility job. Missing tooling is a
failure, never a silent skip. Stored notebook outputs/execution counts are stripped;
execution creates fresh state. Local passing commands are not hosted CI evidence.

For read_only reopen writable; for clock_skew advance an explicit UTC clock beyond
all recorded history/lease floors; for not_found use returned IDs and check as-of
visibility. For profile_too_new install support for the pinned version, not a guessed
migration. human_only_approval/wrong_role require the profile's permitted actor/role,
not forged authentication. unsupported_by_profile means the operation/type is not
available there. For write_failed inspect durable files/HEAD before retrying. Set
Git name/email locally, never globally to fix examples. lease_held requires expiry
or release; expired running work must be reclaimed, not marked complete by an
unrelated actor. Index deletion is safe; deleting graph files is not repair.

Integrations cannot rely on viewer/MCP availability, profile renderers/migrations,
expansion-hook execution, the unsupported reference runner, authenticated actors,
or coordination of simultaneous writes across clones. There is no command sandbox,
core scheduler, agent prompt, discipline-specific behavior or metering. A wrapper
must provide its own concurrency and trust policy without claiming those are core
contracts. Profile authoring and core contributor guidance are outside this manual.
