# Deferred list

Things a gate (G2, G4, G5, G-T) or a review decided not to change now, one per row, until handled.
Things a spec marks as undecided do not go here.

## When to add a row

- G5 findings judged as not fixing, fix next time, worked around, or unchanged by agreement
- Fixes done in two steps: record the second step when the first one merges
- Spec section 4 changed and the contract comments are now wrong, with no comment release in the same batch
- A decision changed a premise and merged code is affected but not fixed this time (`/specflow:change` step 7)
- Small changes the user said need no separate review

## When to check it

- Before a contract's new release: handle the items attached to that contract
- At a dispatch's G5: also review the items for this component marked "review at next G5"
- At dispatch close-out: mark the items this merge resolved as handled

Handled rows are not deleted: set the status to "handled" and write the commit or decision.

## List

"Attached to" says when it gets handled: a contract's next release, a component's next G5, or a specific dispatch.

| # | Date | Source | What | Attached to | Status |
|---|---|---|---|---|---|
| 1 | [FILL] | [FILL] | [FILL] | [FILL] | open |
