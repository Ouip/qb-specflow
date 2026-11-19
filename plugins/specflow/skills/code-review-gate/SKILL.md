---
name: code-review-gate
description: Use when dispatched as the code reviewer (G5), when the user wants a dispatch's diff reviewed, or asks whether the code follows the spec (「程式審」「程式有沒有照規格」).
---

# Code review (G5)

The reviewer must not be whoever wrote this code. Read only; change nothing. G5 runs once per dispatch and is its only review: specs and scenarios get no separate review first (except a change to a frozen contract or protocol), so also report problems in the spec itself (G5-14). Follow the five stages of `specflow:deep-review`; this card gives it the scope, the basis, the checklist and the two tables.

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first.
2. **Scope**: the diff from the prep commit to HEAD; with no diff, the files the user named. Read every touched file in full.
3. **Basis**, all four kinds required:
   - This dispatch's spec: every rule in section 4.
   - The contracts, the implementer rules (`AGENTS.md`), and deferred-list items attached to this component.
   - **Rules in other specs that name this component**: run the impact search from `specflow.md` for the component name, contract file names, function and macro names in the contract, and keywords this code prints or sends. Open every hit and read the section it falls in; every rule there is in scope. The report's basis lists the search commands, then each hit file with the sections read, or why that hit is out of scope.
4. **Check table**: one row per in-scope rule.
   `| Rule ID | Code location file:line | Verification | Verdict | Notes |`
   - Location: trace to the line that actually decides or prints the value. If a caller passes the value in, write `caller:line→callee:line`.
   - Verification: the test named in the rule's Verify cell exists and fails when the rule is broken. With no test, judge from the code itself, write "no test" in Notes, and list it under G5-2. When the product keeps a verify map, run `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/behavior.py <spec> --map <verify map> --mutate --workdir <code repo> --allow <test programs…> --strict`, paste its output, and write each rule's status in this column. Without it, write `REFERENCE_ONLY` for a test you only found and `TEST_PASSED` for one you ran; never more.
   - Verdict: Conforms / Violates / N/A / Cannot judge from diff. Code that differs from the rule Violates; state severity separately.
5. **Style table** (G5-6): one row per `AGENTS.md` rule tagged [G5], checked against full files. Keep it separate from the check table.
6. **Checklist**: G5-1…G5-14 from `${CLAUDE_PLUGIN_ROOT}/gates/G5.md`, plus the product items in the gate-extension file; one row each. Summarize deep-review's five axes and the simplification assessment below; reference existing findings instead of duplicating them.
7. **Report**: follow `${CLAUDE_PLUGIN_ROOT}/templates/reviews/g5.md` and mark the round. The report is the reply itself; write it to a file only when a report path was given. Any Violates row or Fail checklist item means Send back. A personal preference or optional improvement is Advisory, separate from severity and defect counts; it cannot override a violation or cause a Fail by itself. Anything judged "not fixing now, next time" goes on the deferred list (G5-9): the read-only reviewer proposes the entry and the main session writes it. "Cannot judge from diff" rows and unresolved blocking risks go back to the main session; no Pass until resolved.

## Simplification assessment (G5-13)

Assess recently changed code; **no useful simplification** is a valid result. Read its callers, tests and the reason for the current shape (history, comments, platform constraints) before proposing a change. Prefer removing unnecessary branches, duplicate policies or layers when it makes the code easier to follow; fewer lines alone is not a benefit.

For each proposal, record location, before → after, concrete benefit, preserved behavior, checks needed and disposition (implementer / deferred / no change). Preserve inputs and outputs, side effects and their order, error semantics, storage/protocol compatibility, concurrency, timing, blocking and resource ownership. A host test pass alone cannot establish ISR or hardware-timing equivalence. If equivalence is uncertain, leave the code and state the evidence needed. For simplifications already applied in the diff, missing evidence of required behavior is a blocking risk to resolve, not an advisory escape.

The reviewer changes no code. The main session hands accepted in-scope proposals to the implementer; after any edit, run G4 again on the updated revision with the same dispatch baseline, then the fix check in stage 3 of `/specflow:dispatch`; no second G5. Do not edit frozen tests or contracts. Changed requirements go through `/specflow:change`; out-of-scope cleanup gets a separate dispatch or a deferred entry. Neither a simplification proposal nor an absence of proposals requires a new gate or approval round.

## Done when

- The report's basis lists the impact-search commands and every hit file, each with the sections read or the reason it is out of scope.
- Check-table rows = in-scope rules; the report states that number and how it was counted.
- The check table and the style table are two tables; G5-1…G5-14 have one row each.
- The report includes all five quality axes, separates Advisory items, and records the simplification assessment, including a no-change result when appropriate.
- Every Violates row has a rule ID and a `file:line` you reopened in the file itself.
- Reply: the whole report, then the verdict, number of Violates rows and rows handed back to the main session.

| Excuse | Reality |
|---|---|
| This dispatch's spec says nothing about it; no rule found, so mark it unverified and pass | Other specs often set rules for this component (e.g. the field format of test output). Run the step-3 impact search first; a hit is a rule, so judge it |
| Listing the passing items is enough | Without a check table, nobody can see which rule was never reached |
| Every row in 5.1 has a test, so the code follows the spec | Rules in section 4 that 5.1 does not list are reached only by the check table |
| The code looks reasonable and the severity is low, so it conforms | Conformance is judged against the rule, not reasonableness. Write Violates; state severity separately |
| My preferred naming would be cleaner, so send it back | A preference is Advisory; cite an actual violated rule or concrete defect to block |
| Host tests pass, so this ISR refactor preserves behavior | Timing, blocking and ownership need their own evidence; keep unproven proposals out of the code |
