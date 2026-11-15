# Define / Review verification — 2026-11-15

Scope: uncommitted Markdown changes against `cd3da14aecee0ce2734fe430165bc1db26901157`.
No runtime code, plugin dependency or findings schema changed.

## Application baseline and revised instructions

A read-only agent inspected the original cards before modification. A different
fresh-context agent reviewed the final diff and applied the revised instructions
to the same hypothetical cases. These are document/application walkthroughs,
not repeated behavioral trials or product runtime tests.

| Case | Original guidance / gap | Revised instruction and walkthrough result |
|---|---|---|
| Approved threshold edit | No mandatory interview, but no explicit clear-request route | Proceed with scoped edit; no repeated interview or unrelated purpose rewrite |
| Missing board and latency budget | Existing pending-decision and variant protections; no discovery recipe | Read decisions, ask one material question at a time, no invented target or hardware default |
| Sensing, logging and updating bundled | Dependencies expressible, no explicit capability decomposition | Identify independently testable responsibilities and actual contract dependencies; reuse IDs/registry |
| Naming preference only | Rule-based style table, but Low style findings and blocking were not clearly separated | Advisory only, outside defect counts/JSON; cannot itself fail the gate |
| Frozen contract violation | Already explicitly blocked | Still Violates / Send back regardless of severity |
| Applied ISR refactor adds allocation/blocking; host tests pass | Concurrency/resource checks existed; no explicit simplification-equivalence procedure | Blocking risk pending required timing/ownership evidence or correction; fresh G4/G5 after implementer edit |
| Already clear code | No-findings outcome existed, no explicit simplification no-op | Record no useful simplification and reason; no manufactured refactor |

The fresh reviewer also checked dispatch/accept responsibility boundaries,
`review_report.py` compatibility and changed-section G2 reviews. No Critical or
Important findings were reported. Local changes do not require unrelated legacy
purpose backfill. No model-effectiveness or bug-recall improvement is claimed.

## Mechanical checks

- Git Bash `check-templates.sh`: exit 0, `check-templates: ok`.
- Git Bash `lint-generic.sh`: exit 0, `lint-generic: ok`; grep also reported an
  unreadable pre-existing ignored `.pytest_cache` directory. A separate scan of
  all 115 tracked/nonignored Markdown/JSON/shell/Python files using the same
  pattern passed, explicitly excluding ignored caches.
- Checklist/template comparison: G2 has 12 matching unique items; G5 has 13.
- Spec template retains exactly seven numbered top-level sections.
- `git diff --check`: exit 0.
- `python -m unittest discover -s plugins/specflow/tests`: exit 0;
  `Ran 398 tests in 558.829s`, `OK (skipped=4)` (394 passed, 4 skipped).

## Limits

No repeated five-sample wording experiment, hardware timing measurement, product
acceptance or benchmark scoring was performed. Existing Python tests validate
the tooling and fixtures, not whether every future agent follows these cards.
