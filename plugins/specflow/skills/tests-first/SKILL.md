---
name: tests-first
description: Use before a dispatch to decide which integration or end-to-end scenarios to write first, to write scenario code, or when asked what testing must happen before a component is dispatched (「情境先寫」「派工前測試要先做什麼」).
---

# Scenarios first

The user approves the scenario catalog. This card writes code from the catalog; gaps in the catalog become suggestions to the user, never items under "do first".

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first. Find the scenario catalogs and the done-list path each catalog names.
2. List the scenarios whose participants include this component, each in one class:
   - **Complete now**: every other participant is in the done list or in progress → write before dispatch, commit to main.
   - **Not yet complete**: write before the last participant's dispatch.
3. Then list scenarios whose participants are **all already in the done list** but whose code does not exist: "overdue"; fill them in now.
4. Before writing: every participant's contract is frozen. If not, stop and go back to the specs and contracts.
5. Write each from its catalog row: "Input" becomes settings and inputs verbatim; each item of "Expected" becomes one assertion; with participants missing, skip and print "pending activation".
6. Check each against GT-1…GT-4 in `${CLAUDE_PLUGIN_ROOT}/gates/GT.md` and the product's G-T items yourself; then commit to main and freeze. No separate test review: the user approved the catalog, and a scenario that goes red after a dispatch follows the send-back rules of `/specflow:dispatch`. `/specflow:tests-review` runs only when the user asks.

## Done when

- Each "complete now" and "overdue" scenario: the code exists, was checked against GT-1…GT-4, and is on main.
- The integration command in `specflow.md` returns 0 (pending activation counts as passing).
- Reply: each scenario and its class, catalog suggestions for the user to approve.

| Excuse | Reality |
|---|---|
| Add a few scenarios for boundaries the catalog missed | Scenarios need the user's approval; list them as suggestions, not under "do first" |
| A scenario whose participants finished long ago has nothing to do with this dispatch | Overdue and missing means fill it in now |
| It runs, so dispatch | Running proves it passes, not that it tests the row; check GT-1…GT-4 first, then freeze it |
