---
name: accept
description: Use when the implementer reports done and it must be decided whether the work goes on to code review (G4), or when running end-to-end on a real board or fixture and recording manual acceptance (「驗收」「上機」).
---

# Machine acceptance (G4) and on-device

The report is only a lead. The verdict rests only on return values and diffs you produce yourself. Before claiming a pass, follow `superpowers:verification-before-completion`.

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first.
2. **Baseline**: the prep commit (the `prep:` commit on the branch). Every diff runs from it to HEAD, not from main; the spec copies and implementer rules it brought in are not the implementer's changes.
3. **Item by item**: G4-1…G4-6 from `${CLAUDE_PLUGIN_ROOT}/gates/G4.md`, plus the G4 items in the gate-extension file. One row each: `| Item | Command | Return value | Verdict |`.
   - G4-1: run every line of spec section 6 and every host-runnable layer under "Test layers and commands" in `specflow.md` yourself. If a command's tool is missing, look for it in the plugin or the product repos first; if it truly is not there, mark Fail and say what is missing. Save the output; the report gives the paths.
   - G4-2: `git diff --name-only <prep commit>..HEAD`, each path checked against spec section 7.
   - G4-3: `git diff <prep commit>..HEAD -- <contract paths>` is empty, and the files are byte-identical to spec section 3.
   - G4-4: each claim in the report is checked against the measurements above; `done` with unmet items is a Fail.
   - G4-5: repos or files outside the workspace: `git status --porcelain` matches the record from before dispatch.
4. **Verdict**: all Pass → go to G5 (`/specflow:code-review-gate`). Any Fail → send back, handing the failing commands and output to stage 3 of `/specflow:dispatch`.
5. **On-device stage** (when a real board or fixture is needed): run the end-to-end command in `specflow.md`; the manual checklist follows spec 5.5. Record results in the manual acceptance record (template `${CLAUDE_PLUGIN_ROOT}/templates/manual-acceptance.md`). When something goes red, use `mattpocock-skills:diagnosing-bugs`.

## Done when

- The G4 table has one row per item; each command has a return value and an output location.
- Reply: verdict (to G5 / send back), failing items with evidence, output file paths.
- On-device stage: every record row has a result.

| Excuse | Reality |
|---|---|
| A diff from main is more complete | Files from the prep commit then look out of bounds; diff from the prep commit |
| This done criterion's tool is missing, so skip it for now | A criterion that was not run cannot pass; missing is a Fail, stating what is missing |
