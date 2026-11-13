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
   | `workflow.json`, `components.json` | `<spec folder>/`, same names |
   | `scenarios.md` | `<spec folder>/T-int.md`; with an end-to-end layer, also `T-e2e.md` |
   | `settings.project.json` | `.claude/settings.json` at the spec repo root; one in each code repo |
   | `CLAUDE.code.md` | `CLAUDE.md` in each code repo (skip a code repo that does not exist yet) |

   `spec.md`, `manual-acceptance.md` and `reviews/` are not copied: the skills that use them read them from the plugin. `workflow.lock.json` is not a template: `lock-write` writes it (step 6).
3. `.claude/settings.json` already exists → leave it; print its `extraKnownMarketplaces` and `enabledPlugins` blocks for the user to merge. Set `ref` to `v<plugin version>`.
4. `.gitignore` at the spec repo root: missing → create it with the line `specflow.local.json`; exists → leave it and print that line for the user to add.
5. Fill `specflow.md`: header `core:` = `version` in `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`; the fields the user gave; decisions table, deferred list, couplings table, gate-extension file and scenario catalogs = the actual paths from step 2.
   - `workflow.json`: `repos` = the spec repo with `"home": true`, plus one entry per code repo, keyed by its name; `capabilities.dispatch.repos` = those code repo names (no code repo yet → drop the code repo entry and leave that list empty); `handoff.file` = the handoff file, relative to the spec repo root.
   - `components.json`: the `states` names = the "Component states" row of `specflow.md`; `repos` = one entry per code repo, same keys as `workflow.json`, each `{"integration_branch": <the same branch as in workflow.json>}`.
6. Reply: one line per file (created / exists, skipped), then the next steps (`<python>`: the `python:` header line of `specflow.md`):
   1. Fill the remaining [FILL] in `specflow.md` (N left), `workflow.json` and `components.json` (M left).
   2. Write the product's gate items in the gate-extension file.
   3. `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/workflow_config.py" --product <spec folder> regen` writes `specflow.local.example.json` at the spec repo root; copy it to `specflow.local.json` there and set this machine's absolute paths. It is never committed; every worktree of the spec repo needs its own.
   4. `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/workflow_config.py" --product <spec folder> lock-write` writes `workflow.lock.json`; commit it with `specflow.md`.
   5. `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/workflow_config.py" --product <spec folder> doctor` returns 0.
   6. Write the first spec with `/specflow:spec-write`.

Done when: `specflow.md` exists with its three header lines; every pre-existing file is unchanged; the reply has each file's result and the next steps.

| Excuse | Reality |
|---|---|
| The existing `AGENTS.md` must not be touched, so put another copy elsewhere | There is one set of implementer rules; keep the existing one and print "exists, skipped" |
| `.gitignore` exists, so add the line to it | An existing file is never changed; print the line for the user to add |
| The user named no code repo, so create `fw/` | Not given means [FILL]; create no folder |
| Copying every template is more complete | The skills read the spec and report templates from the plugin; copies drift when the plugin upgrades |
