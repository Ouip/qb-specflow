# Define and Review Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Execution is authorized by the user's approval of the linked design.

**Goal:** Integrate selective Define and Review practices into existing Specflow cards and gates.

**Architecture:** Extend existing Markdown instructions and templates together. Preserve script interfaces, findings schema, seven-section specs, existing gate IDs and plugin dependencies.

**Tech Stack:** Markdown workflow cards, Bash repository checks, Python unittest suite.

**Spec:** ../specs/2026-11-15-define-review-design.md (approved 2026-11-15).

## Global Constraints

- Review remains read-only and separate from implementation.
- Existing gate violations remain blocking; preferences do not become defects.
- Preserve firmware timing, concurrency, ownership and error behavior during simplification.
- No new dependency, workflow registry, required interview round or numeric defaults.
- Product budgets and manual evidence keep their existing locations and sources.

## Review Focus

- Clear approved requirements proceed without a new interview.
- Changed-section reviews do not require unrelated legacy specification backfill.
- Preferences do not suppress actual style, contract or safety violations.
- Host tests do not establish timing or ISR equivalence.
- A no-op simplification outcome passes without manufacturing changes.

## Execution record and tasks

Ruling: implement in the existing `portable-core` feature checkout; this is a coordinated document-only change, and the approved design is already here. No branch merge, commit, push or release is required for the requested reviewable result.

### Task 1: Define

Files: `skills/spec-write/SKILL.md`, `gates/G2.md`, `skills/spec-review/SKILL.md`, `templates/spec.md`, `templates/reviews/g2.md`, all under `plugins/specflow/`.

- [x] Record application baseline before editing.
- [x] Add conditional discovery, alternatives and capability decomposition to spec-write.
- [x] Add purpose prompts and append G2-12; update G2 consumers together.
- [x] Apply clear, ambiguous and multi-capability scenarios after editing.

### Task 2: Review and simplification

Files: `skills/deep-review/SKILL.md`, `skills/code-review-gate/SKILL.md`, `gates/G5.md`, `templates/reviews/g5.md`, all under `plugins/specflow/`.

- [x] Record baseline for preferences, violations, unsafe simplification and no-op.
- [x] Add five-axis review and advisory vs blocking rules without changing JSON.
- [x] Add simplification assessment, implementer handback and fresh G4/G5 requirements.
- [x] Append G5-12 and G5-13 and update consumers together.
- [x] Run the same application scenarios with modified cards.

### Task 3: Documentation and validation

Files: `README.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, this plan, design and a verification record in `docs/superpowers/`.

- [x] Document integration and copied-template migration; attribute concepts with source links.
- [x] Run `bash plugins/specflow/scripts/check-templates.sh`, `bash plugins/specflow/scripts/lint-generic.sh`, `python -m unittest discover -s plugins/specflow/tests`, and `git diff --check`.
- [x] Check gate/template/card ID consistency and inspect full diff.
- [x] Obtain fresh read-only review; resolve material findings and rerun affected checks.
- [x] Record actual results and limits; mark design implemented and tasks complete.

Completed 2026-11-15: all three tasks implemented. Full suite: 398 tests,
394 passed and 4 skipped, exit 0. Checklist/template and seven-section checks
passed. Fresh review found no Critical/Important issues. Application results and
mechanical-check limits: `../2026-11-15-define-review-verification.md`.

Application scenarios are instruction walkthroughs, not repeated behavioral experiments, runtime product tests or evidence of improved defect recall. Use no new mirror-the-text unit tests for this document-only change.
