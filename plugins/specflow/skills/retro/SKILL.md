---
name: retro
description: Use when a gate let a problem through, when the user wants to look back on a stretch of work, or to decide whether an improvement belongs in the core or the product (「回顧」「關卡沒擋下」).
---

# Retro

The engine is `mattpocock-skills:retro`; only the user can invoke it, so read its body and follow it: under `~/.claude/plugins/cache/`, find `*/mattpocock-skills/*/skills/engineering/retro/SKILL.md`. Not found → stop and ask the user to install mattpocock-skills.

1. Read `specflow.md` in the product's spec folder (missing, or no `format: 2` in its header → ask the user to run `/specflow:init-product` first).
2. Find candidates per retro's steps; tag each with its retro category (Navigation, Automated checks, Coding standards, Global AGENTS.md, Tool economy, No-ops, Information access).
3. Tag each with a layer too: **core** = any firmware product would hit it; change specflow's gates, skills, templates or scripts (open an issue). **product** = only this product's chip, framework, tools or decisions cause it; change `specflow.md`, the gate-extension file, `AGENTS.md` or a product skill. An item with both layers splits into two, one layer each.
4. Mechanical items (fixed spellings, banned APIs, file locations) become scripts or lint; only judgment calls become checklist items.
5. Give them to the user ordered by severity. Propose only; change nothing.

Done when: every item has a retro category, core or product, a one-sentence reason and one improvement; items with both layers are split.

| Excuse | Reality |
|---|---|
| The product only supplies examples, so the whole item is core | If the product's own list or rule is missing something (e.g. a framework version's banned-API list), that half changes in the product layer; split it |
