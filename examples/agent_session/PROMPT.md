# Ten-step researchgraph agent session

This is a small example consumer workflow, not core server guidance or an
unassisted product/model recording. The deterministic SDK replay in
`tests/test_agent_session.py` checks the same workflow. Passing it does not
satisfy the real Claude Code/Codex CLI recording requirement.

## Human preparation (before the session)

Install researchgraph with its MCP extra in the interpreter used by the agent.
These scripts import the installed package, not a source-tree path injection.
Use absolute script paths when running outside the source checkout. Create a
fresh project directory, initialise its Git repository, and configure a real
`user.name` and `user.email` before seeding. For example:

```sh
mkdir /tmp/research-session
git -C /tmp/research-session init
git -C /tmp/research-session config user.name "Researcher"
git -C /tmp/research-session config user.email "researcher@example.test"
python /absolute/path/to/examples/agent_session/seed.py /tmp/research-session \
  --manifest /tmp/research-session-seed.json
```

The optional `--now RFC3339` selects the seed clock; otherwise UTC wall time at
whole-second precision is used. The API is `seed(root, *, actor, now)`. It returns
a JSON-serialisable manifest with `seed_head`, `hypothesis`, `prediction`, a seed
actor/time, and baseline file hashes. The CLI prints that manifest and saves it
without overwriting an existing file. Keep an independent trusted copy outside
the agent's writable checkout. Do not put manifests under `graph/` or
`artifacts/`. The Hypothesis and Prediction have already been approved by the
human. No later human graph write is allowed during this exercise.

Register an installed stdio server with the agent product, using the absolute
project directory and a fixed declared agent identity:

```sh
rgraph --project /tmp/research-session --as-agent session-worker mcp --preset essential
```

`full` also works and is the default. Do not launch as human, override actor or
role through tool arguments, edit managed YAML directly, or turn off commits.
Give the agent the project path and the two prerequisite IDs from the manifest.
Use the client/product's supported discovery negotiation; legacy initialize-only
clients are refused. The server does not execute programs or write your artifact
bytes for you: use the agent's ordinary local filesystem capability for those.

## Agent instructions

Perform these ten steps without human intervention. Use researchgraph MCP tools
for every graph operation. Treat failed tool calls as failures, not successes.
If providing explicit `now`, use increasing whole-second RFC3339 UTC times after
the seed clock; otherwise let the server use its clock. Do not approve any node.

1. **Show the Hypothesis.** Call `show` with `id` equal to the manifest's
   `hypothesis`. Confirm it is active. Read the approved Prediction identified
   by `prediction` as needed. The small sample illustrates workflow, not a
   scientific conclusion.
2. **Propose a Task addressing the Prediction.** Call `propose` with
   `type="Task"`, a descriptive title, and
   `edges=[{"type":"addresses","to":"<prediction>"}]`. Save its returned ID.
3. **Check readiness.** Call `ready`; the Task must appear in `items`.
4. **Claim the Task.** Call `claim` with `node` set to its ID. Confirm a running
   status and a lease held by your fixed agent identity.
5. **Write a real artifact and complete.** Create
   `artifacts/<task-id>/measurement.csv` under the project with these exact UTF-8
   bytes, including the final newline:

   ```csv
   sample,value
   control,2
   treatment,4
   ```

   Call `complete` with `node`, an honest `summary`, and
   `artifacts=["artifacts/<task-id>/measurement.csv"]`. Confirm it is done and
   its lease is cleared. This is a deterministic local example, not a report of
   an external experiment or model run.
6. **Propose a Result and attach evidence.** Call `propose` with `type="Result"`,
   a title describing the sample, `fields={"runs":1,"held_out":false}`, and
   `edges=[{"type":"derived-from","to":"<task-id>"}]`. Create a separate
   `artifacts/<result-id>/measurement.csv` with the same real bytes. Call
   `attach` with `node="<result-id>"` and its own artifact path. Paths must be
   inside the owning node's artifact directory. Save the returned SHA-256.
7. **Request approval by proposing a Claim.** Call `propose` with `type="Claim"`,
   a modest title, and these edges (substitute IDs, path and digest):

   ```json
   [
     {"type":"cites","to":"<result-id>","fields":{"artifact":"artifacts/<result-id>/measurement.csv","sha256":"<digest>"}},
     {"type":"supports","to":"<hypothesis>","fields":{"strength":"weak"}}
   ]
   ```

   Confirm the Claim is `proposed` with no approvals. Proposing the Claim is the
   approval request. Do not call `approve`, impersonate a human or mark it active.
8. **Confirm the queue.** Call `queue` and verify the Claim remains the sole
   pending approval request. Leave it there for a future human decision outside
   this session.
9. **Record an intentional failure.** Propose a second Task addressing the same
   Prediction and claim it. Call `fail` with its `node`, an honest `summary`, and
   string arguments `step="validate second sample"`,
   `check="sample count must be at least 2"`, `measured="1"`,
   `error="insufficient samples"`. These are scalar arguments, not a nested
   `fields` object. Confirm the Task is failed with no lease. The operation
   creates the separate explanatory Failure and `explains` edge; do not fake a
   second execution or retry it.
10. **Read the digest.** Call `digest`. In its `items`, confirm no running Tasks,
    one pending Claim, one recent failed Task and one Claim. Report the completed
    and failed Task IDs, Result and Claim IDs, and artifact paths. Stop without
    making further graph writes.

## Independent post-session check

The human/operator runs this read-only checker only after the agent stops:

```sh
python /absolute/path/to/examples/agent_session/check.py /tmp/research-session \
  --manifest /tmp/research-session-seed.json
```

The API is `check(root, manifest) -> summary`; invalid sessions raise
`AssertionError`. The CLI prints JSON with `ok` and exits nonzero on failure.
It reads native graph YAML, actual artifact bytes and Git history, never SQLite
or the replay's returned outcome IDs. It requires unchanged approved seed
records, the exact session relationships, two terminal Tasks and their execution
events, recorded committed evidence, a Failure with step/check, and a still
proposed Claim. It rejects dirty managed paths and checks every reachable
post-seed managed commit's author, including a human edit later restored by an
agent. This intentionally narrow example does not support merged session
histories or additional graph nodes/relationships.

Actors and Git authors are self-declared identities, not cryptographic evidence
of actual human involvement or a particular model. The trusted manifest, source
checkout and Git history must not be replaced by the agent. Actual product
compatibility and unassisted performance require separate real product/model
versions, configuration, consent-safe transcripts and checker output; none are
claimed by this example or its deterministic replay.
