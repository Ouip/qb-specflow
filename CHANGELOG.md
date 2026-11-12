# CHANGELOG

## 0.0.3 — 2026-11-12

From a product (2026-11-12): `pickup` step 1 read the whole `specflow.md` (4,033 characters in that product), but pickup uses only the format line and the Language, Spec folder, Code repos and Handoff file rows. The rest entered context at the start of every session.

- `pickup` step 1: reads only those lines, with `grep -E '^format:|^\| (Language|Spec folder|Code repos|Handoff file) \|' specflow.md` (PowerShell: `Select-String -Pattern` with the same regex). In that product: 4,033 → 633 characters. The regex depends on those four field names and the `| Field | Value |` row form; a template change to either must change the regex too.

### Template changes

- `specflow.md`: `core: 0.0.3`.
- `settings.project.json`: `ref` `v0.0.3`.

### Format changes

None.

## 0.0.2 — 2026-11-12

From a product (2026-11-12): the product turned superpowers off in its project settings, because superpowers' session-start text ("invoke a skill before any response") conflicts with the rule that general skills run only when a specflow step calls them. specflow then failed to load in every new session: `Dependency "superpowers@claude-plugins-official" is disabled`.

- `plugin.json`: superpowers is no longer a dependency.
- `dispatch` stage 2: the sub-agent implementer's prompt states the test-first rule itself (write each 5.1 row's test first and see it fail, then make it pass) instead of pointing at `superpowers:test-driven-development`. A product can preload a TDD skill into its implementer agent.
- `accept`: the pointer to `superpowers:verification-before-completion` is replaced by its rule: run the checks yourself, now, and quote their output.

### Template changes

- `specflow.md`: `core: 0.0.2`.
- `settings.project.json`: `ref` `v0.0.2`.
- `CLAUDE.docs.md`: a new requirement is settled with the user, then `/specflow:spec-write` (was `superpowers:brainstorming`).

### Format changes

None. A product may still enable superpowers; specflow no longer needs it.

## 0.0.1 — 2026-11-10

First release: 13 skills, four gate checklists (G2, G-T, G4, G5), templates, check scripts (`coverage.py`, `behavior.py`, `couplings.py`, `review_report.py`, `bench.py`), a SessionStart version-check hook and a local review benchmark.

### Template changes

- `specflow.md`: `core: 0.0.1`.

### Format changes

None.
