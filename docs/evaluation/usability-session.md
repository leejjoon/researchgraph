Status: approved for collection by the product owner, 2026-10-22

# Outside-researcher viewer usability protocol

participant_count_target: 3
participant_count_achieved: 0

This is a protocol and blank report schema, not evidence of completed sessions.
No participants or sessions have actually run. Recruit at least one consenting
researcher **outside the team**; aim for three distinct outside researchers.
Report the genuine achieved count, including incomplete/withdrawn attempts
separately where consent permits. Do not turn the target into an achieved claim.
Local browser tests, team demonstrations and replay are not outside-user proof.

## Review and consent before recruitment/capture

| Review | Reviewer | Decision | Date / evidence |
| --- | --- | --- | --- |
| Product-owner protocol and task review | Jae-Joon Lee (product owner; not the author) | approved | 2026-10-22; committee review (Claude Fable 5.1, Codex GPT-6-Astra) applied in this commit; reviewed at 190edc5 |
| Synthetic seed and privacy/capture review | Jae-Joon Lee (product owner; not the author) | approved | 2026-10-22; committee review (Claude Fable 5.1, Codex GPT-6-Astra) applied in this commit; reviewed at 190edc5 |

The product owner reviews the task wording, synthetic starting state, success
criteria, stopping rules and privacy plan before use. The protocol's author does
not sign the table; the product owner signs it and records that they did not
author it. Signing authorizes recruitment and collection, not any outcome.
Obtain explicit consent **before recording**: purpose, what screen/audio and
interaction data will be recorded, who can access originals and public derivatives,
retention/deletion dates, voluntary participation, a named study and privacy
contact, a withdrawal deadline before publication, and separate agreement to
screen capture, to optional think-aloud audio (no webcam) and to public
derivatives, explaining that published Git copies cannot reliably be recalled.
State whether institutional ethics review applies and record the determination
or exemption reference before recruitment. Offer the participant review of any
derivative before publication. Offer stopping without penalty. Store consent/contact records
separately under restricted access, linked only by a pseudonymous participant ID.
Use no real research identifiers, patient data, credentials or secrets. Anonymize
researcher/institution/project names, personal paths and Git identities in reports.
If a participant withdraws or sensitive material appears, stop recording and follow
the agreed withdrawal/deletion policy; do not publish the sensitive evidence.

Record only the dedicated synthetic project window. Close personal applications
and disable notifications. The loopback launch URL fragment is a credential, as
are cookies/session tokens; never publish them or film the initial URL exchange.
Obtain a safe authenticated view before screen capture. Keep access limited to
consent-authorized reviewers, not a public bucket. Privacy-review screenshots,
recordings, transcripts and native snapshots before any sharing/commit, including
filenames and embedded metadata. Hash restricted originals and redacted derivatives
separately; document redactions without disclosing removed values.

## Prepare and review a fresh synthetic seed

Use an installed interpreter with the viewer extra and the absolute installed
`rg` console script (not ripgrep). Record source commit, package/Python/browser
versions, OS, viewport, server command and redacted configuration. Each participant
gets a fresh project with the same reviewed starting content; never reuse a prior
participant's mutated graph. The following preparation is human operator work,
not a participant task. Replace the absolute interpreter/source/evidence paths;
choose a new project and manifest filename each time.

```sh
mkdir /tmp/researchgraph-usability-p01
git -C /tmp/researchgraph-usability-p01 init
git -C /tmp/researchgraph-usability-p01 config user.name "Study researcher"
git -C /tmp/researchgraph-usability-p01 config user.email "study@example.test"
/absolute/path/to/venv/bin/python /absolute/path/to/researchgraph/examples/agent_session/seed.py \
  /tmp/researchgraph-usability-p01 \
  --manifest /absolute/operator-only/evidence/usability-p01-seed.json
```

That script seeds approved synthetic Hypothesis/Prediction prerequisites. Add a
synthetic failed Task and its registered artifact using the installed native API:

```sh
/absolute/path/to/venv/bin/python - <<'PY'
import json
from datetime import UTC, datetime
from pathlib import Path
from researchgraph import Project
from researchgraph.model import Actor

root = Path('/tmp/researchgraph-usability-p01')
manifest = json.loads(Path('/absolute/operator-only/evidence/usability-p01-seed.json').read_text())
actor = Actor('human', 'Study researcher', 'study@example.test')
now = datetime.now(UTC).replace(microsecond=0)
with Project.open(root) as project:
    task = project.propose(
        'Task', 'Validate synthetic second sample',
        edges=[{'type': 'addresses', 'to': manifest['prediction']}],
        actor=actor, now=now,
    )
    project.claim(task['id'], actor=actor, now=now)
    relative = f"artifacts/{task['id']}/sample.csv"
    artifact = root / relative
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b'sample,value\ncontrol,2\n')
    project.attach(task['id'], [relative], actor=actor, now=now)
    project.fail(
        task['id'], 'Synthetic sample count validation failed',
        step='validate second sample', check='sample count must be at least 2',
        measured='1', error='insufficient samples', actor=actor, now=now,
    )
print(json.dumps({'failed_task': task['id'], 'hypothesis': manifest['hypothesis'],
                  'artifact': relative}))
PY
/absolute/path/to/venv/bin/rg --project /tmp/researchgraph-usability-p01 serve --no-open
```

Retain actual preparation output/exit codes and native baseline HEAD/file hashes.
If preparation fails, fix and review the seed before recruiting; do not seed or
repair it during the timed tasks. An operator reviews the committed YAML/history
and viewer to confirm failed status, the separate Failure with step/check/measured/
error and `explains` edge, the registered downloadable artifact, and the approved
Hypothesis. Record the reviewed seed version/hash and IDs in the operator packet.
The example agent-session checker is intentionally narrow and is **not** an audit
of this different usability graph. Do not report its result as usability proof.

The server binds loopback only, so the participant works at the operator-controlled
machine, in person or through a remote-desktop session they control; an outside
researcher's own laptop cannot reach it. Open the launch URL there, complete the
one-use session exchange, and remove credential exposure before capture. Serve as the configured Git human (default),
without `--read-only`, because the next two tasks require human writes. The launch
fixes identity and project; roles are declared profile roles, not authentication.
Do not run a runner/scheduler. Keep a baseline native snapshot and preserve the
post-task graph so actual state, not just UI messages, can be verified afterward.

## Moderator procedure and clocks

Provide a short neutral orientation: this is a research graph viewer, all data are
synthetic, the participant can think aloud, stop or decline any task. Do not
provide a click-path demonstration or explain which controls solve a task. Give
only one task card at a time. Begin its monotonic clock when the participant has
received/read the card and says they are ready; record that instant and UTC start.
End at the participant's declared completion, abandonment, safety stop or 600
seconds, whichever is first. Record UTC end and **task_elapsed_seconds** from the
monotonic clock. The target is **600 seconds per task**, not an adjusted cutoff.
Also record overall session start/end and overall duration, including orientation,
transitions and debrief; do not substitute overall duration for task timings.

Moderate without coaching. Neutral prompts such as “What are you thinking?” may
be logged, but no hints about controls, fields or success criteria. On a request
for help say “Please proceed as you would on your own, or stop this task.” Record
the request and response verbatim. Any procedural/safety intervention is logged
with time, task, content and effect; do not relabel assisted success as unassisted.
If a task times out or fails, preserve its state, mark the outcome and move on
only when safe. Do not manually repair the graph to disguise failure. A later
task blocked by prior state is recorded as blocked, not omitted. An optional
post-task demonstration must be outside timed scoring and distinctly labelled.
Stop the session on withdrawal, privacy issue, unsafe action or unusable service;
retain the real stop reason, not a newly weakened definition of success.

## Task cards and independent state verification

### 1. Inspect a failed node (inspect)

Participant card: “Find the failed synthetic sample-validation Task. Explain what
failed, at which step and check, and locate and download its registered artifact.
Tell us what
the evidence does and does not show.”

Success requires finding the failed node, reading the linked Failure's
`step='validate second sample'` and `check='sample count must be at least 2'`,
identifying measured 1/insufficient samples, and opening/downloading the registered
artifact through the inspector. After the timed attempt, compare answers and
artifact bytes/hash with the native baseline. Record missing or misleading fields,
unsafe downloads and navigation confusion as findings, not successes.

### 2. Request re-run with instructions (re-run)

Participant card: “Request a replacement for that failed Task with the instruction
‘Collect at least two synthetic samples before validating.’ Check what changed
and whether any work has actually executed.”

Success requires using the viewer's re-run with instruction action and recognizing
that it records a request, not execution. The observer verifies native `supersede`:
old Task retired, new replacement ID, `supersedes` relation to the old Task, and a
separate Instruction with `instructs` relation to the replacement containing the
requested text. Verify no automatic execution, no new execution results and no
inherited running lease/budget. Save before/after HEADs and actual IDs. A toast
alone is not sufficient. A commit/hook error may leave durable records: preserve
and inspect actual history before any later deliberate action; never auto-retry.

### 3. Expand a Hypothesis (expand)

Participant card: “Request expansion of the synthetic Hypothesis with a brief hint
about a follow-up sample. Find the created request and explain whether expansion
has run.”

Success requires the viewer Expand action and identification of the created
expansionTask (record the returned Task ID under that report label). In native
records this is a Task with `fields.kind='expansion'` and `fields.targets`
containing the Hypothesis ID, not a new node type named expansionTask. Verify the
hint/body, creation history and no execution: no automatic runner/hook, claim or
completion. Do not score merely opening the dialog as completion or count an
expansion request as finished research.

Use read-only native inspection after scoring to validate each claimed result;
retain relevant YAML, relationships, history, artifact hashes and screenshots.
Do not let operator inspection provide hints during a participant's task.

## Actual-session report schema (empty templates, not sessions)

No rows below represent participants or measured results. Fill only from actual,
consent-permitted observations; record failures and incomplete attempts as well
as successes. Never insert guessed timings, synthetic quotes or fake recordings.

| Participant/session field | Actual value to collect |
| --- | --- |
| Pseudonymous ID / outside-team eligibility | Restricted consent reference; no public identifying affiliation |
| Seed / environment | Source commit, reviewed baseline HEAD/hash, versions and command |
| Consent / owner review | Actual decision references, capture scope and retention deadline |
| Overall timing | UTC start/end, monotonic overall_duration_seconds, stop reason |
| Capture | Real screenshot/recording/transcript paths, gaps, SHA-256 and privacy review |
| Native verification | Before/after HEADs, failed/replacement/Instruction/expansion Task IDs |

| Task | clock_start_utc | clock_end_utc | task_elapsed_seconds | Outcome | Assistance/interventions | Native verification/evidence |
| --- | --- | --- | --- | --- | --- | --- |

The empty task table is intentional. Outcomes are `success-unassisted`,
`success-assisted`, `failed`, `timed-out`, `blocked`, `withdrawn` or
`capture-incomplete`, with observed reasons. Record all assistance including none
only when capture supports it. Keep per-task 600-second target comparisons even
when unsuccessful; report overall duration separately. Report how many distinct
eligible consenting outside researchers actually attempted tasks and how many
completed all three, plus failures/withdrawals consistent with consent. Achieved
counts change only with actual evidence, never with scheduled invitations.

| Finding ID / task | Observed behavior and impact | Severity | Reproduction steps / evidence | Disposition / owner |
| --- | --- | --- | --- | --- |

This empty findings table is a template, not “no findings.” Use severity
critical/high/medium/low with a rationale (privacy/data-integrity loss, task block,
misunderstanding, or friction). Include expected versus actual behavior, native
state where relevant, reproducible steps, evidence timestamps and disposition:
open, accepted limitation, fixed-awaiting-retest or verified-fixed. Preserve
adverse findings; do not silently remove them after a repair.

## Evidence naming, review and retention

Name files `usability-<pseudonymous-id>-<attempt>-<task-or-session>-<kind>.<ext>`;
keep restricted consent mapping elsewhere. Maintain a SHA-256/byte-size inventory
for every actual recording, screenshot, transcript, result/log, native snapshot,
seed and redacted derivative. Hash actual bytes (for example
`sha256sum /absolute/path/to/actual-file`), never placeholder files. Document each
derivative's source hash, redactions and privacy reviewer decision. Record the
custodian, consent-agreed deletion date, authorized access list and withdrawal
process before collecting. Remove restricted originals/backups at expiry and
retain only permitted deletion receipts. After privacy and outcome review, commit
the consent-safe derivatives (filled session and task tables, findings, redacted
screenshots and native verification) under `docs/evaluation/sessions/` with the
file names above, and update the evidence index and
`participant_count_achieved`; recordings and restricted originals are never
committed. No empty fake session reports. This protocol creates
no consent, recording, measured result or product-owner approval.
