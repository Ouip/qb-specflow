---
name: tests-review
description: Use when dispatched as the test reviewer (G-T), or when integration or end-to-end scenario code is written and needs review (「測試審」).
---

# Test review (G-T)

The reviewer must not be whoever wrote this test. Read only; change nothing.

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first.
2. **Scope**: this scenario's code. **Basis**: its catalog row, the participants' contracts, the G-T items in the gate-extension file.
3. **Item by item**: GT-1…GT-4 from `${CLAUDE_PLUGIN_ROOT}/gates/GT.md` plus the product items, one row each, notes citing `file:line`. GT-1 maps each item of "Expected" to an assertion; GT-2 runs this scenario alone once and records the return value.
4. **Report**: follow `${CLAUDE_PLUGIN_ROOT}/templates/reviews/gt.md`; use the path the user gave, otherwise `<code repo>/docs/reviews/T-<ID>-gt.md`; mark the round.
5. **Verdict**: any Fail means Send back. Faults in the catalog or the spec itself go in a separate section for the main session to take to the user.

Done when: one row each for GT-1…GT-4; every "Expected" item maps to an assertion or says "no assertion"; the reply gives the verdict and the report path.

| Excuse | Reality |
|---|---|
| The test passes, so it follows the catalog | Passing does not mean the assertions cover every "Expected" item; map them one by one |
