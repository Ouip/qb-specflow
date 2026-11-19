---
name: spec-review
description: Use when a change to a frozen contract or protocol needs its spec review (G2), when dispatched as the G2 reviewer, or when the user asks for a spec review (「規格審」「重審規格」).
---

# Spec review (G2)

The reviewer reads the spec and changes nothing. Follow the five stages of `specflow:deep-review`; this card gives it the scope, the checklist and the report format.

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first.
2. **Scope and basis**: this spec (for a change, only the sections on the impact list); the dependencies' contracts; the decisions table; the couplings rows with this spec on either side; the gate-extension file. Specs designed in the same batch are reviewed together.
3. **Run the two scripts first** and paste their output into the report verbatim:
   - `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/coverage.py <spec> --catalog <scenario catalogs from specflow.md…>`
   - `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/couplings.py <couplings table> --specs <spec folder> --code <code repos from specflow.md…>`, plus `--values <value file> [--cpp <preprocessor>]` when the product keeps a value file
   A script that returns 1 makes its item Fail.
4. **Item by item**: G2-1…G2-12 from `${CLAUDE_PLUGIN_ROOT}/gates/G2.md`, plus the product items in the gate-extension file. One row each: Pass / Fail / N/A (with a reason); notes cite `file:line`. For every couplings row, open the opposite spec and reread it; write "coupling #n checked" or state the mismatch. G2-12 checks the definition in scope; a local edit does not reopen settled decisions or require unrelated purpose backfill.
5. **Report**: follow `${CLAUDE_PLUGIN_ROOT}/templates/reviews/g2.md` and mark the round. The report is the reply itself; write it to a file only when a report path was given.
6. Verdict: any Fail means Send back. What the user must settle (`(new)` rules, changes involving a decision) gets its own section.

## Done when

- The report has one row each for G2-1…G2-12 and every product item; rows G2-10 and G2-11 paste the script output.
- Every Fail has a `file:line` or a section number.
- Reply: the whole report, then the verdict, number of Fail items and what the user must settle.

| Excuse | Reality |
|---|---|
| The opposite spec is not in the folder, so list it under "not reviewed" | A basis that points at something missing is itself a Fail (G2-11); put it in the checklist |
| No check tools in sight, so this can only be verified on device | `coverage.py` and `couplings.py` ship in the plugin; run them in step 3 |
| A few free-form findings are enough | Without the item-by-item checklist, nobody can tell which items were reviewed and which were skipped |
