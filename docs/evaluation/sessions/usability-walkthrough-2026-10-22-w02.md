1. **Browser tool used**

   Supplied headless Chromium driver: `~/.cache/researchgraph-ci/usability/w02-scratch/browse`. The page loaded successfully and displayed “Authenticated.” I used the same session throughout.

2. **Tasks**

   **Task 1: inspect**
   
   clock_start_utc: `2026-10-22T15:34:29Z`  
   clock_end_utc: `2026-10-22T15:35:25Z`  
   outcome: **success**

   Steps:
   - Opened “Validate synthetic second sample,” marked “failed” (`ta-4cyydl2n`).
   - Opened “Full node record” and pressed End; neither exposed additional readable details through the tool.
   - Downloaded “artifacts/ta-4cyydl2n/sample.csv.” The browser confirmed `sample.csv`, 23 bytes, saved in its downloads directory.
   - Opened “Failure: Validate synthetic second sample,” then “Failures — rolling 7 days.”

   Answer: Synthetic sample-count validation failed at **“validate second sample.”** The check was **“sample count must be at least 2,”** the measured count was **1**, and the error was **“insufficient samples.”** This evidence shows an unmet sample-count requirement; it does not establish whether the treatment hypothesis is true or false. I downloaded the artifact but did not inspect its contents.

   Think-aloud notes: Finding the failed task and artifact was straightforward. I expected the task details to explain the failure; instead, the monitoring disclosure provided the readable explanation.

   **Task 2: re-run**
   
   clock_start_utc: `2026-10-22T15:35:29Z`  
   clock_end_utc: `2026-10-22T15:36:26Z`  
   outcome: **success**

   Steps:
   - Reopened “Validate synthetic second sample.”
   - Clicked “Re-run with instruction.”
   - Filled “Instruction” with “Collect at least two synthetic samples before validating.”
   - Clicked “Apply Re-run with instruction.”
   - Enabled “Show retired,” then “Expand history.”
   - Opened “Activity — native running records.”

   Answer: Created replacement task `ta-2hdtyhqo` and instruction `in-rak7vace`, whose title contains the requested instruction. The original task became **retired**. The replacement appeared under ready records; monitoring showed **ready: 1, running: 0, claims: 0**, and no running items. There was no evidence that the replacement had executed.

   Think-aloud notes: The form had more options than I needed, including “Fields,” “Inherit edges,” and several “Set” checkboxes. “Active” initially sounded like execution, but the ready list clarified its state. Recovering the original task required both history-related controls.

   **Task 3: expand**
   
   clock_start_utc: `2026-10-22T15:36:30Z`  
   clock_end_utc: `2026-10-22T15:37:08Z`  
   outcome: **success**

   Steps:
   - Opened “Treatment doubles the control measurement” (`hy-64l7wvuo`).
   - Clicked “Expand.”
   - Filled “Hint” with “Collect a follow-up synthetic sample comparing treatment and control.”
   - Clicked “Apply Expand.”
   - Found and opened “Expand: Treatment doubles the control measurement” in the ready list.

   Answer: Created expansion request `ta-loy4cnll`. It appeared under ready records, with **ready: 2, running: 0, claims: 0**, and no running items. The request exists; there was no evidence that expansion had run.

   Think-aloud notes: The short hint form was easy to use. Finding the new request in the ready list helped distinguish requesting expansion from completing it.

3. **Findings**

   1. **High — Detail visibility through the supplied browser tool.** Node controls appeared, but selected-node explanatory text did not appear in snapshots, even after opening “Full node record.” I expected readable task details. I cannot determine whether this limitation belongs to the viewer or the driver.
   2. **Medium — “Active” is ambiguous.** Ready tasks were labeled “active” in the graph. I expected an explicit execution state such as “Ready — not running.”
   3. **Medium — Recovering the original task required two controls.** “Show retired” alone did not reveal it; “Expand history” did. I expected “Show retired” to suffice or explain the dependency.
   4. **Medium — Technical terminology slowed interpretation.** “Native digest,” “Authoritative readback,” and “Claims — native where membership” were unfamiliar. I expected plain descriptions of status and ownership.
   5. **Low — Re-run form complexity.** “Fields,” “Inherit edges,” and “Set” checkboxes complicated a simple instruction request. I expected the instruction field first, with advanced options separated.
   6. **Medium — Action labels understate request semantics.** “Re-run” and “Expand” can suggest immediate execution. I expected “Request replacement” and “Request expansion,” followed by explicit pending-state confirmation.

4. **Overall**

   A researcher new to the tool could complete these tasks, but would need to reconcile graph labels with monitoring information. Clearer execution-state wording and reliably readable task details would make the workflow easier to trust.
