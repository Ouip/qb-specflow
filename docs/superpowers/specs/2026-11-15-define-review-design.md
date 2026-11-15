# Define and review improvements

Status: approved and implemented on 2026-11-15. Validation is recorded in
`../2026-11-15-define-review-verification.md`.

## Intent

Bring selected Define, code-review and code-simplification practices from
addyosmani/agent-skills into Specflow's existing firmware workflow. The user
requested this addition after comparing the projects. The integration details
below are the implementer's proposal, not decisions already made by the user.

## Approaches

1. Recommended: integrate the practices into existing skills, gates and report
   templates. Keeps a single source of truth and the current workflow.
2. Add agent-skills as a runtime dependency. Gives upstream updates, but overlaps
   specification and review routing and introduces another lifecycle.
3. Add separate Define and Simplify workflows. Makes them directly invocable,
   but adds handoffs and requires maintaining another set of artifacts.

The proposal uses approach 1. No new plugin dependency or mandatory gate.

## Define

Extend `spec-write` before drafting, and the Purpose section of `templates/spec.md`:

- Read decisions, contracts, existing specs and product constraints before asking.
- State the user/problem, intended outcome, measurable success, scope and excluded
  work. Mark assumptions and unresolved decisions explicitly.
- Ask one material question at a time only when its answer changes the contract,
  behavior, hardware binding or acceptance criteria. Already settled requests
  proceed without an interview, confidence percentage or extra approval round.
- For genuinely open design choices, compare viable alternatives, their costs
  and failure assumptions; recommend the smallest sufficient solution. Settled
  designs do not require fabricated alternatives.
- If a request spans independently testable capabilities, identify component
  responsibilities, dependencies and implementation order using existing specs
  and the component registry; do not create a second registry or PRD format.
- Product-specific budgets (RAM, flash, latency, power, etc.) need a source and
  measurement method. No generic numeric defaults. Machine checks remain in
  section 6; hardware/manual verification stays in 5.5; restrictions in section 7.

Add a G2 item checking that purpose, boundaries and success are concrete and
consistent with the behavior rules, with no unresolved decision silently given
an implementation default. Update the G2 report template and spec-review's
checklist references together.

## Code review

Extend `deep-review` and the G5 card/report with five explicit quality dimensions:
correctness, readability/simplicity, architecture, security and performance.
Reuse existing checks rather than duplicate correctness/risk tables. Firmware
performance review covers bounded work, hot-path allocation, blocking and ISR
constraints against actual product requirements, with measurements for claims.

Separate a finding's severity from whether it blocks the gate. Confirmed spec,
contract, product-rule and safety violations remain blocking under existing
gates. Personal preference and optional improvements cannot block an otherwise
conforming change. Explain each structural concern with a concrete consequence
and a proposed change; do not impose line-count or abstraction-count quotas.

Keep the existing findings JSON schema and evidence requirements. Record
advisory observations in the report; do not manufacture CONFIRMED defects for
preferences. Deferred work follows the existing deferred-list rules. Review
remains read-only and independent from implementation.

## Simplification

Add an explicit simplification assessment to G5, with "no useful simplification"
as a valid result. Reviewers propose changes; implementers apply them within
the dispatch's allowed paths, followed by G4 and G5 on the updated revision.

- Understand the original reason, callers, history and platform constraints first.
- Prefer fewer concepts, branches and unnecessary layers over fewer lines.
- Preserve inputs, outputs, error semantics, side effects and their order,
  protocol/storage compatibility, concurrency, timing and resource ownership.
- Preserve frozen contracts and tests. Passing host tests alone does not establish
  equivalence for timing, interrupt or hardware-sensitive changes.
- Changes to required behavior go through `change`; work outside the allowed scope
  requires a separately scoped dispatch or a deferred item.
- Do not require a refactor when existing code is already clear or the benefit
  cannot be demonstrated.

## Delivery and verification

Update affected skill cards, gates and their report templates in sync, plus README,
CHANGELOG and THIRD_PARTY_NOTICES. Retain the seven-section specification format,
existing gate identifiers and findings schema. Document migration of copied
templates and product impact of the added review items. Use original wording and
credit upstream concepts; include the upstream license if adapting its text.

Validate template and generic-lint scripts and run the existing Python unit suite.
Exercise the instructions with focused scenarios: a clear request that needs no
interview; an ambiguous hardware request; a multi-component request; an optional
style suggestion; a real gate violation; a proposed simplification that changes
timing or error behavior; an already-simple implementation. Report which checks
were actually executed and which are document walkthroughs. No claim of improved
defect detection or token savings without comparative evidence.

## Sources (consulted 2026-11-15)

- https://github.com/addyosmani/agent-skills/blob/main/skills/interview-me/SKILL.md
- https://github.com/addyosmani/agent-skills/blob/main/skills/idea-refine/SKILL.md
- https://github.com/addyosmani/agent-skills/blob/main/skills/spec-driven-development/SKILL.md
- https://github.com/addyosmani/agent-skills/blob/main/skills/constraint-driven-development/SKILL.md
- https://github.com/addyosmani/agent-skills/blob/main/skills/code-review-and-quality/SKILL.md
- https://github.com/addyosmani/agent-skills/blob/main/skills/code-simplification/SKILL.md
