---
name: tests-first
description: Use before a dispatch to decide which integration or end-to-end scenarios to write first, to write scenario code, or when asked what testing must happen before a component is dispatched (「情境先寫」「派工前測試要先做什麼」).
---

# Scenarios first

The user approves the scenario catalog. This card writes code from the catalog; gaps in the catalog become suggestions to the user, never items under "do first".

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first. Find the scenario catalogs and the done-list path each catalog names.
2. List the scenarios whose participants include this component, each in one class:
   - **Complete now**: every other participant is in the done list or in progress → write before dispatch, pass G-T, commit to main.
   - **Not yet complete**: write before the last participant's dispatch.
3. Then list scenarios whose participants are **all already in the done list** but whose code does not exist: "overdue"; fill them in now.
4. Before writing: every participant's contract is frozen. If not, stop and go back to the specs and contracts.
5. Write each from its catalog row: "Input" becomes settings and inputs verbatim; each item of "Expected" becomes one assertion; with participants missing, skip and print "pending activation".
6. Hand each to another sub-agent for `/specflow:tests-review`. On send back, fix per the findings and review again; on pass, commit to main and freeze.

## Done when

- Each "complete now" and "overdue" scenario: the code exists; its G-T report (template `${CLAUDE_PLUGIN_ROOT}/templates/reviews/gt.md`, one row each for GT-1…GT-4) says Pass; it is on main.
- The integration command in `specflow.md` returns 0 (pending activation counts as passing).
- Reply: each scenario and its class, G-T report paths, catalog suggestions for the user to approve.

| Excuse | Reality |
|---|---|
| Add a few scenarios for boundaries the catalog missed | Scenarios need the user's approval; list them as suggestions, not under "do first" |
| A scenario whose participants finished long ago has nothing to do with this dispatch | Overdue and missing means fill it in now |
| It runs, so dispatch | A scenario that has not passed G-T may test the wrong thing; review it first, then freeze it |
