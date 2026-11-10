---
name: init-product
description: Use when a new product is joining specflow, or a product's spec folder has no `specflow.md` yet (「接上新產品」).
---

# Set up a new product

Never overwrite or merge an existing file: print "<path> exists, skipped". Values the user did not give are written as [FILL]; create no folder or repo the user did not mention.

1. Ask once (skip what was already given): product name, spec folder, code repos (name and path; "none yet" if none), chip and framework, test layers and commands, implementer, impact search commands, language for reports and replies.
2. Copy from `${CLAUDE_PLUGIN_ROOT}/templates/`:

   | Template | Goes to |
   |---|---|
   | `specflow.md` | `<spec folder>/specflow.md` |
   | `CLAUDE.docs.md` | `<spec folder>/CLAUDE.md` |
   | `AGENTS.md` | `<spec folder>/AGENTS.md` (the only copy of the implementer rules; dispatch copies it into the code repo) |
   | `decisions.md`, `deferred.md`, `couplings.md`, `gates-ext.md` | `<spec folder>/`, same names |
   | `scenarios.md` | `<spec folder>/T-int.md`; with an end-to-end layer, also `T-e2e.md` |
   | `settings.project.json` | `.claude/settings.json` at the spec repo root; one in each code repo |
   | `CLAUDE.code.md` | `CLAUDE.md` in each code repo (skip a code repo that does not exist yet) |

   `spec.md`, `manual-acceptance.md` and `reviews/` are not copied: the skills that use them read them from the plugin.
3. `.claude/settings.json` already exists → leave it; print its `extraKnownMarketplaces` and `enabledPlugins` blocks for the user to merge. Set `ref` to `v<plugin version>`.
4. Fill `specflow.md`: header `core:` = `version` in `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`; the fields the user gave; decisions table, deferred list, couplings table, gate-extension file and scenario catalogs = the actual paths from step 2.
5. Reply: one line per file (created / exists, skipped), then the three next steps:
   1. Fill the remaining [FILL] in `specflow.md` (N left).
   2. Write the product's gate items in the gate-extension file.
   3. Write the first spec with `/specflow:spec-write`.

Done when: `specflow.md` exists with its three header lines; every pre-existing file is unchanged; the reply has each file's result and the three next steps.

| Excuse | Reality |
|---|---|
| The existing `AGENTS.md` must not be touched, so put another copy elsewhere | There is one set of implementer rules; keep the existing one and print "exists, skipped" |
| The user named no code repo, so create `fw/` | Not given means [FILL]; create no folder |
| Copying every template is more complete | The skills read the spec and report templates from the plugin; copies drift when the plugin upgrades |
