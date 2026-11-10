# [FILL: product name] specs

- This folder is the single source of truth. When two documents disagree, stop and ask the user; do not pick one.
- Scope: [FILL: the product in one sentence, its boards, chips and framework]
- Product settings are in `specflow.md`; workflow steps and gates are run by the specflow plugin's skills.

## Discussion and design

1. Answer the question asked: when a question has more than one reading, answer the most direct, practical one first; propose new features after.
2. Start from the simplest design: give the approach that just meets the need; add no fields, encodings or flows nobody asked for.
3. Plain words: explain each term in one sentence the first time it appears.
4. Simple first, complex apart: list more complex approaches as "optional improvements", each with one sentence on why.
5. No invented requirements: behavior the specs and decisions do not state is not filled in.
6. When readings differ a lot in scope, ask one question; when they are close, follow rule 1.
7. Look wide at architecture options: draw on general firmware practice, not only the few patterns common in the framework at hand.

## What to read and which skill to use

| Situation | Read first | Skill |
|---|---|---|
| Starting, picking up | handoff file | `/specflow:pickup` |
| New requirement, design change | decisions table | `superpowers:brainstorming`; once settled, `/specflow:spec-write` |
| A decision changes an existing premise; a contract needs a new version | couplings table, deferred list, contract change rules | `/specflow:change` |
| Writing or changing a spec | spec template, couplings table | `/specflow:spec-write` |
| Spec review (G2) | gate-extension file | `/specflow:spec-review` |
| Integration and end-to-end scenarios | scenario catalogs | `/specflow:tests-first`, `/specflow:tests-review` |
| Dispatch, acceptance, code review | `specflow.md`, gate-extension file | `/specflow:dispatch` |
| Wrapping up, switching sessions | — | `/specflow:wrapup` |
| A gate missed a problem | — | `/specflow:retro` |

## Source markers

The marker in parentheses after a rule says where it came from.

| Marker | Meaning |
|---|---|
| `(#n)` | item n of the original requirements |
| [FILL] | [FILL: the format of decision IDs] |
| `(new)` | added by the spec itself, with no upstream source; spec review looks at it closely |
