---
name: dispatch
description: Use when the user wants a component built, dispatched or re-dispatched, or a fix dispatch opened, or when post-merge close-out is unfinished (「派工」「做某個部件」「重派」).
---

# Dispatch

Four stages; check each stage's exit before the next. The main session does not write the component's code; the implementer named in `specflow.md` does.

**Before starting**: read `specflow.md` in the product's spec folder (missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first). This spec's G2 report says Pass; the integration scenarios this dispatch activates have passed G-T (`/specflow:tests-first`). If anything is missing, fill it in first; do not dispatch.

## Stage 1: Prepare the workspace

1. In the code repo, branch `comp/<component>` from main. For several components at once, one worktree each; above the parallel limit in `specflow.md`, ask the user first.
2. Copy in the specs (the spec folder's component specs and scenario catalogs), the implementer rules (`AGENTS.md`) and this release's contracts; commit with a message starting `prep:`.
3. Record: the prep commit hash (the G4 baseline); `git status --porcelain` of every repo outside the workspace that will be touched (compared in G4-5).

**Exit**: the prep commit exists; the contract files are byte-identical to spec section 3; both records are in the handoff file or the reply.

## Stage 2: Implement

Per "Implementer" in `specflow.md`:

- **Sub-agent**: the prompt says: read this spec in `docs/` and implement it fully; change only the paths allowed in spec section 7; do not change a character of the contracts or frozen tests, and report a scenario you believe is wrong; write each 5.1 row's test first and see it fail, then make it pass; follow `andrej-karpathy-skills:karpathy-guidelines` before writing code; when done, run every line of spec section 6; report in the product's format, listing anything unfinished as unmet items.
- **Another tool**: use the command in `specflow.md`, carrying the points above verbatim.

**Exit**: a well-formed report exists. Then run `/specflow:accept` yourself; do not trust the report.

## Stage 3: Send back

G4 or G5 fails → append the failing commands, output and review findings to the end of the next prompt (not to the spec) and run stage 2 again:

```
The previous attempt did not pass (attempt n, limit 3). Why:
<failing output and review findings>
```

- **If attempt 3 still fails, stop and ask the user**: usually the spec is wrong.
- The fault is in another, already-merged component (a newly active scenario goes red and, per the spec, the other side violates it) → pause this dispatch (it does not count as an attempt), move the other component back to "In progress" and open its fix dispatch; after it merges, resume here from G4. If you cannot tell whose fault it is → ask the user.

**Exit**: n ≤ 3, and each attempt's reason is in the handoff file.

## Stage 4: Close out

Only when G4 and G5 (`/specflow:code-review-gate`) both pass:

1. Merge to main; add the component to the done list (the path the scenario catalog names) in the same commit as the merge.
2. Write the newly frozen contracts back into spec section 3 in full.
3. Recompute every component's state per the component states in `specflow.md`: this merge may activate other components' scenarios.
4. Deferred list: mark what this merge resolved as handled; everything new from G5 is on the list.
5. Spec backfill: take each G5 "spec backfill" finding (G5-11) through `/specflow:change`, or put it on the deferred list; none is left open before step 8.
6. "Extra steps" dispatch close-out in `specflow.md`: "none" → skip; a skill → invoke it; not found → write "skipped, `/x:y` not found".
7. Run the consistency lint; push.
8. Report to the user: what was done, test evidence, the manual acceptance the user must do (spec 5.5); its results are recorded with the on-device stage of `/specflow:accept`.

**Exit**: the merge commit includes the done-list change; lint returns 0; push returns 0; the deferred list is updated; every spec-backfill finding went through `/specflow:change` or onto the deferred list.

| Excuse | Reality |
|---|---|
| `specflow.md` does not list the stages, so infer them from the spec | The stages are on this card; `specflow.md` supplies only commands and names |
| Sending back means asking the implementer to try again | Attach the failing output and record the attempt number; stop at attempt 3 |
