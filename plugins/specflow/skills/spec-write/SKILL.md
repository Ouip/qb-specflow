---
name: spec-write
description: Use when writing a new component spec, adding sections to a spec, changing an existing spec's rules or test list, or designing a contract (「寫規格」「改規格」「設計契約」).
---

# Writing a spec

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → stop and ask the user to run `/specflow:init-product` first.
2. A new spec starts from `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`, seven sections: purpose, dependencies, interface, behavior rules, tests, done when, prohibited. An existing spec keeps its own numbering and format.
3. Read the basis: the decisions table, the dependencies' contracts, and the couplings rows involving this component.

## Define before drafting

Read the existing product settings, specs and code before asking. In section 1, state who or what uses the component, the problem and intended outcome, observable success, and the scope and exclusions. Reference settled decisions; distinguish sourced facts, proposed assumptions and decisions still needed.

- **Clear request with settled decisions**: proceed with the scoped edit. Reuse existing purpose and boundaries; no interview or extra approval round.
- **A missing answer changes behavior, a contract, hardware binding or acceptance**: ask one material question at a time, with a recommendation and its reason. Record unresolved choices for the user; do not silently invent a requirement. An unattended implementer reports the blocker rather than inventing answers.
- **A design choice is genuinely open**: compare viable alternatives by constraints, cost, failure assumptions and how success would be checked; recommend the smallest sufficient option. Settled designs need no artificial alternatives.
- **The request bundles independently testable capabilities**: identify each component's responsibility, contract dependencies and build order before drafting its spec. Reuse existing component IDs and registry; propose new IDs for new components and use `registry.py add` when registration is enabled. Do not infer dependency direction from names alone or create a second index.

For quality budgets (memory, storage, latency, power), record the source, target or measured baseline, verification method and whether it blocks or warns. Product-wide budgets belong in `specflow.md` or its gate-extension file; component requirements belong in section 4 with Source and Verify. Do not supply generic numeric defaults. Put executable acceptance checks in section 6, hardware/manual checks in 5.5 and restrictions in section 7. Unsettled targets stay decisions for the user.

## Section 4: one row per rule, all four cells filled

`| # | Rule | Source | Verify |`. Write each rule and its verification together: every rule names the test that fails when it is broken.

Rules are written one of two ways only: a table with a `#` column, or a numbered list (`1.`) ending in `[verify: …]`. `coverage.py` does not check a table without `#` or a `- ` list and prints a `warning:` line for each; rewrite them, or pass `--strict` to fail on them.

- **Verify** holds one of: `5.1 G<n>` (a 5.1 group, heading `#### G<n>`), a scenario ID (e.g. `I01`, in a scenario catalog named in `specflow.md`), `5.5` (manual), `untested: <reason>`. If you cannot name a test, add a group to 5.1, or write `untested:` with the reason.
- **Source**: a decision ID; rules the spec invents are marked `(new)`. What the decisions table has not settled is listed as "for the user to decide", not filled in.
- Numbers, strings and lengths state max, min, negative and empty; state machines state "B happens while A is in progress".
- Claims about drivers, libraries or hardware cite source `file:line` or a datasheet page; if none can be found, write "pending hardware" and add a row to 5.5.
- **Undecided variant**: a value that holds for one hardware variant names that variant. For a variant not decided yet, write "pending hardware" and make that variant's build fail (`#error "<value> for <variant> not decided"`, listed in section 6 and in the allowed paths of section 7). No default, no blank.

## Sections 5 and 6

- Each 5.1 group gets a `#### G<n>` heading; each table row is "input → expected". 5.2 and 5.4 list scenario IDs only.
- Each line of section 6 is a command plus its result: `<command>` returns 0; `grep -rE "<regex>" <path>` has no hits. Two lines are always there: the product's consistency lint returns 0; `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/coverage.py <this spec> --catalog <scenario catalogs…>` returns 0 (it decides "every rule names a test that exists"; whether that test catches a broken rule is `behavior.py`'s job at G5).
- Judgment calls go in 5.5 (manual) or section 7 (prohibited); section 6 holds commands only.

## Contracts and couplings

- The full contract goes in section 3 after `<!-- header: path -->`; after the user approves, mark it "frozen v1". Changing a frozen contract goes through `/specflow:change`.
- If this spec shares a quantity (size, clock, millisecond value, coordinate, format) with another component, add a couplings row; for an existing row, reread the opposite side once.

## Done when

- `coverage.py` returns 0; the product lint returns 0; every line of section 6 is a command.
- Reply to the user: which sections changed, how many rules, what the user must decide, new couplings rows. Next step: `/specflow:spec-review`.
- Section 1 makes the outcome and scope concrete; assumptions and unresolved decisions are visible. For a local edit, reuse the existing definition rather than rewriting unrelated sections.

| Excuse | Reality |
|---|---|
| Writing "every rule is covered by at least one test" into the done criteria is enough | No command can judge that sentence. A Verify cell per rule plus `coverage.py` returning 0 can judge that each rule names a test; `behavior.py` judges whether the test fails when the rule breaks |
| The contract is frozen, so leave the undecided variant's value blank | A blank lets the implementer pick. Undecided means that variant's build fails, listed as pending hardware |
| Every request needs a requirements interview | Read settled decisions first; ask only when a missing answer changes what is built or accepted |
