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

## Adapted or copied text

This version copies no third-party text. If any is ever adapted, its source and the full MIT text go here.

## Not third party

`skills/deep-review` was written by this repo's owner and needs no license notice.
