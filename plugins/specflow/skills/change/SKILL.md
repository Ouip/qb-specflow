---
name: change
description: Use when the user changes a decision, requirement, hardware, contract or scenario and the specs and code must follow, or asks what a change would affect (「改了決定」「這個改了會影響哪裡」).
---

# Change

The impact list is this change's work list: nothing off the list changes, and every row on it gets a result.

1. Read `specflow.md` in the product's spec folder. Missing, or no `format: 2` in its header → the reply's first sentence is "Run `/specflow:init-product` first."; change no files; stop.
2. **Record the decision**: add a row to the decisions table (ID, content, what it supersedes). No ID, no further steps. When the user asks for no file changes, write the ID as "to be recorded", skip step 5 onward, and write no file. The reply is then, in order: the impact table itself (all six columns, one row per location, a few words per cell), the step-4 questions, and the five items of "Done when" below, each with its current state.
3. **Scan the impact** from all four sources into one impact list (file: `<spec folder>/changes/<date>-<decision ID or short name>.md`):
   `| Location | Says now | Change to | This time / deferred | Source | Result |`
   - a **Text**: every changed symbol, value, model number and keyword, searched in the specs and code repos with the impact search in `specflow.md`. Search each other spelling of the same quantity too (`320×240`, `320x240`, `QVGA`).
   - b **Couplings table**: run `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/couplings.py <couplings table> --specs <spec folder> --list`. For each table row where a changed quantity appears, **every line it prints for that row is its own impact row**, even with no text hit. Source: "coupling #n".
   - c **Merged code**: files hit in the code repos, including tools, generators and test data. "This time / deferred" says "fix dispatch" or "deferred list".
   - d **Deferred list**: items attached to these files or contracts, with the reason to handle them now or not.
   Rows judged "not affected" stay on the list, with the reason as the result. Under the list, paste `couplings.py`'s output and return code verbatim (command under "Done when"; it only reads, so run it even when changing no files).
4. **Show the user the list**. Anything involving a decision (a new value, how a variant binds, a controller model) becomes a question; wait for the answer.
5. **Change in one batch**: spec sections, contracts (released per the contract change rules in `specflow.md`), scenario catalogs, couplings table (new couplings get rows; rows checked on both sides get this decision ID as "Last checked"). Fill each row's result as you finish it.
6. **Review only the changed sections**: `/specflow:spec-review`, scope = the sections on the impact list.
7. **Code**: affected merged code gets a fix dispatch through `/specflow:dispatch`, or a deferred-list row saying when it will be handled.

## Done when

- The impact list (its file, or the reply when changing no files) is a table with all six columns; every row has a Result.
- For each couplings-table row from 3b, every line `couplings.py --list` printed for it appears in the Location column.
- The consistency lint in `specflow.md` returns 0 (if it says "none", write "none").
- `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/couplings.py <couplings table> --specs <spec folder> --code <code repos…>` returns 0.
- The step-6 G2 report says Pass.
- The decisions table has this entry.

Missing any one means not done: the handoff file says which step it stopped at.

| Excuse | Reality |
|---|---|
| Text search found nothing, so it is not affected | Places that use the same quantity under another spelling (e.g. a pixel estimate computed from a side length) surface only through the couplings table |
| This spec's behavior does not change; it only gains a sentence | Put it on the list and fill "This time / deferred"; a no-change judgment also gets a reason |
| Test data and generators are a separate matter | They are generated from the changed quantity; they are code-repo impact too |
