# THIRD_PARTY_NOTICES

## Dependencies (declared under `dependencies` in `plugin.json`; no text copied)

| Plugin | Author and source | License | Used for |
|---|---|---|---|
| `mattpocock-skills` | Matt Pocock, https://github.com/mattpocock/skills (via `claude-plugins-official`) | MIT | `retro` (`/specflow:retro` reads its steps and follows them), `diagnosing-bugs` (when something goes red on device) |
| `andrej-karpathy-skills` | https://github.com/forrestchang/andrej-karpathy-skills (marketplace `karpathy-skills`) | MIT | `karpathy-guidelines` (loaded by the sub-agent implementer before writing code) |

Used during development, not in `dependencies`: `superpowers:writing-skills` (Jesse Vincent, https://github.com/obra/superpowers, MIT) and `mattpocock-skills:writing-for-agents` (this repo's skills are written and tested with them).

## Borrowed concepts (own wording)

- Matt Pocock's `code-review`: "report the two axes separately" → G5's check table and style table are two tables.
- superpowers' `writing-skills`: the "Excuse | Reality" table format in each skill; the test method of running a baseline before writing the skill.
- Matt Pocock's `writing-for-agents`: the terms "pointer" and "information hierarchy".
- Matt Pocock's `retro`: `/specflow:retro` tags items with retro's own category names.
- Addy Osmani / agent-skills (MIT, https://github.com/addyosmani/agent-skills),
  consulted 2026-11-15: selected Define and Review concepts, expressed in original
  wording and integrated into Specflow rather than installed as a dependency:
  - [`interview-me`](https://github.com/addyosmani/agent-skills/blob/main/skills/interview-me/SKILL.md),
    [`idea-refine`](https://github.com/addyosmani/agent-skills/blob/main/skills/idea-refine/SKILL.md),
    [`spec-driven-development`](https://github.com/addyosmani/agent-skills/blob/main/skills/spec-driven-development/SKILL.md),
    [`constraint-driven-development`](https://github.com/addyosmani/agent-skills/blob/main/skills/constraint-driven-development/SKILL.md):
    focused discovery, alternatives/assumptions, scope and capability decomposition,
    and sourced measurable quality budgets → `spec-write`, spec section 1 and G2-12;
    discovery before questions also informs interactive `init-product` setup.
  - [`code-review-and-quality`](https://github.com/addyosmani/agent-skills/blob/main/skills/code-review-and-quality/SKILL.md):
    five quality axes, actionable structural advice and non-blocking preferences →
    `deep-review`, G5-12 and its report template.
  - [`code-simplification`](https://github.com/addyosmani/agent-skills/blob/main/skills/code-simplification/SKILL.md):
    understand the existing reason before changing code, preserve behavior, prefer
    clarity to line counts and limit scope → G5-13 and its read-only assessment.

## Adapted or copied text

This version copies no third-party text. If any is ever adapted, its source and the full MIT text go here.

## Not third party

The original two-track, five-stage `skills/deep-review` method was written by this
repo's owner. Its added five-axis/advisory guidance borrows concepts credited above.
