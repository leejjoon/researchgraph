## 1. Browser tool used

I used the gstack `/browse` skill (headless Chromium driven from the shell). The launch link loaded, the page showed "Authenticated." and the URL settled on the bare host without the token. All three cards were done in the same tab. No command-line tools, source, docs, or on-disk files of the application were touched. The only file I wrote outside the browser was the downloaded artifact itself, into a scratch directory.

## 2. Task cards

### Task card 1: inspect

- clock_start_utc: 2026-10-22T15:23:47Z
- clock_end_utc: 2026-10-22T15:25:08Z
- outcome: success

**Steps.** The landing page has three stacked panels: "Live monitoring", "Research graph", and "Node inspector", plus a "Review queue 0" button. The card list under Research graph showed four cards; one read "ta-ui67qkad Validate synthetic second sample failed". I clicked that card. The Node inspector filled in with the Task. Under "Incidents" it showed an "explains" edge from a Failure node with four labelled rows: Check, Error, Measured, Step. I clicked the "Full node record" and "Full edge record" groups to read the raw records. Under "Artifacts" there was a raw JSON block and a link labelled "artifacts/ta-ui67qkad/sample.csv". I downloaded that link through the browser. I tried the "Full history result" group twice and it never showed content.

**Answer.** The Task "Validate synthetic second sample" failed at step "validate second sample" on the check "sample count must be at least 2". The recorded error is "insufficient samples" with a measured count of 1. The Approvals block shows the same human actor claimed the Task and then failed it at the same timestamp with the note "Synthetic sample count validation failed". The registered artifact downloaded as a 23-byte CSV. Its contents are a header row and one data row, "control,2". Its SHA-256 matched the hash registered in the inspector.

The evidence shows: exactly one sample was present and the check required two, so the failure is consistent with the artifact. It does not show: any treatment measurement, where the sample came from, who or what produced it, what the validation procedure actually did, or any log of the run. The Task's own "Evidence" section says "No items", so the artifact is the only material.

**Think-aloud.** Finding the failed Task was easy because the status word is on the card and the monitoring digest said "failed_24h: 1". The inspector is a very long vertical page of raw JSON blocks under headings. I had to read JSON to see who failed it and when. The Failure's four labelled rows were the clearest thing on the page. I expected a "Download" button; instead there is a plain path link, which did work. The History group looked like it should open and did nothing, so I could not see a timeline. Three "Full ... record" groups look identical and I had to open each to learn which was which.

### Task card 2: re-run

- clock_start_utc: 2026-10-22T15:25:13Z
- clock_end_utc: 2026-10-22T15:27:06Z
- outcome: success, with one misstep I corrected inside the card

**Steps.** With the failed Task selected I clicked "Re-run with instruction". A form appeared with Title, Body, Fields, Inherit edges, and Instruction boxes, each paired with a "Set ..." checkbox and the note "Omitted unless Set X is checked". I typed the instruction into the Instruction box, then clicked "Set Instruction". I pressed "Apply Re-run with instruction". The inspector status changed to "ta-ui67qkad · retired" and a raw JSON block for a new Task "ta-iwzi4j4v" appeared. I clicked "Refresh monitoring" and the graph "Refresh". The old card vanished from the list, a new active Task card took its place, and the digest went to "ready: 1". I opened the new Task. Its "Instructions" section said "No items". I opened the "Event log" group and read the envelopes: Task retired, Task created, two edges created, nothing else. No Instruction node existed. Typing in the box had auto-ticked "Set Instruction", and my click had unticked it. Since I was still within the card, I clicked "Instruct" on the replacement Task, entered the same text, and pressed "Apply Instruct". The inspector then showed an Instruction node "in-7mhk6uvo" under Instructions, and the event log gained a node and an "instructs" edge.

**Answer.** What changed: the original Task is now retired. A replacement Task with the same title exists, carries a "supersedes" edge to the original and a fresh "addresses" edge to the Prediction, and has no artifacts or approvals. After my correction it has one linked Instruction with the requested text. Nothing has executed. The form itself says "This does not execute work." The digest shows "running: 0", "claims: 0", and the replacement sits in "Native ready records — unclaimed or reclaimable" with the flag "ready: true". The failed count stayed at 1 because the Failure node is unchanged.

**Think-aloud.** The "Set X" checkbox pattern caught me. Typing silently ticked the box, and my deliberate tick turned it off, so the instruction was dropped with no warning. Nothing on the result told me the instruction was missing. I only found out by reading the raw event log. I expected the apply result to say in words "Created Task ta-iwzi4j4v, retired ta-ui67qkad, Instruction: none". Instead it was a JSON blob at the top of the inspector. The retired original disappeared from the list, and I had to guess that "Show retired" would bring it back.

### Task card 3: expand

- clock_start_utc: 2026-10-22T15:27:13Z
- clock_end_utc: 2026-10-22T15:28:28Z
- outcome: success

**Steps.** I clicked the "hy-icmqmint Treatment doubles the control measurement" card, then "Expand". The form had a Hint box with a "Set Hint" checkbox. This time I checked the box state before and after typing: it was unticked before and ticked after, so I left it alone and pressed "Apply Expand". A JSON block for a new Task "ta-wmfsbqyu" titled "Expand: Treatment doubles the control measurement" appeared, with my hint as its body and fields "kind: expansion, targets: [hy-icmqmint]". I pressed the graph Refresh and the monitoring Refresh. The accessibility listing of cards did not include the new Task, even with the Type filter set to Task. The monitoring panel's "Native ready records" list did show it. A screenshot afterwards shows the card rendered in the Task column under the replacement Task, so the card exists; my tool's text listing missed it, and I could not tell from the page why.

**Answer.** The created request is Task "ta-wmfsbqyu", visible in the Node inspector result block, in the monitoring ready-records list, and as a card in the Task column. Expansion has not run. The form says it does not execute work, the digest shows "running: 0" and "ready: 2", and the Task has no lease, approvals, or artifacts. The Hypothesis progress row does not count the expansion Task as a member, because the Task is tied to the Hypothesis only by a "targets" field, not an edge.

**Think-aloud.** The flow was the same as the re-run form, so it felt familiar. The inspector kept showing the previous action's Instruction JSON at the top while I was looking at the Hypothesis, which made me doubt which node I had selected. After applying, I had no obvious way to jump to the new Task; the result block has no link. I found it through the monitoring list rather than the graph.

## 3. Findings

1. **Typing into a field auto-ticks its "Set X" checkbox, so a deliberate tick cancels the value.** Severity: high. The instruction was silently dropped and the result gave no hint. Expected: either no checkbox, or a checkbox that stays ticked once text is present, plus a result message naming what was and was not created.
2. **Action results are raw JSON blocks.** Severity: high. After every apply, the inspector shows a JSON dump with no sentence saying what happened and no link to the new node. Expected: a plain summary such as "Created Task X, retired Y, Instruction attached: yes/no" with clickable ids.
3. **Stale result block persists across node selection.** Severity: medium. The Instruction JSON from the re-run stayed at the top of the inspector while the Hypothesis was selected. Expected: the block clears or is labelled "last action".
4. **"Full history result" group never showed content.** Severity: medium. Two clicks on it did nothing visible. Expected: a timeline of claim, fail, retire, supersede events.
5. **Artifact download is a bare path link next to a JSON blob.** Severity: medium. It works, but the hash, size, and who added it are only readable in JSON. Expected: a row per artifact with name, size, hash, and a "Download" button.
6. **Retired nodes vanish from the list with no notice.** Severity: medium. The original Task disappeared after re-run; only the small "Show retired" checkbox restores it. Expected: a note "1 retired node hidden" near the list.
7. **Three identical "Full node record" and "Full edge record" groups under Incidents.** Severity: low. You must open each to learn which edge or node it is. Expected: the id and type in the group label.
8. **Expansion Task is not counted as a Hypothesis member.** Severity: low. Hypothesis progress ignores the expansion request because it is linked by a field rather than an edge, so the panel looks unchanged after the request. Expected: expansion requests shown in the progress row or a note explaining why not.
9. **Card list did not appear in my tool's accessibility listing after the expansion.** Severity: low, unverified. The screenshot shows the card, so this may be my browser tool rather than the app. Worth checking whether late-added cards get accessible names.

## 4. Overall

A researcher new to this tool could complete the inspect card, since the failed Task and its Failure rows are easy to find and the artifact link works. The re-run and expand cards are riskier. The "Set X" checkbox pattern and JSON-only feedback make it easy to submit a request without the instruction or hint and not notice. Both actions are clearly labelled as not executing work, which answers the "has anything run" question well.
