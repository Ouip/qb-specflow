---
name: init-product
description: Use when installing or setting up a product for specflow, resuming incomplete setup, or checking an existing setup (「初始化」「設定專案」「接上新產品」).
---

# Interactive product setup

Own setup from discovery through verification. The user installs the plugin and
runs `/specflow:init`; you generate the configuration and run the checks. Do not
hand back a template-filling or command-running checklist.

## 1. Discover before asking

Read the installed plugin version and locate its real scripts/templates directory.
Inspect the current repo, existing `specflow.md` / `workflow.json`, instructions,
build/test manifests and CI. Use read-only git commands to find repo roots and
integration branches (remote HEAD first, then existing main/master; do not assume
the current feature branch is the integration branch). Inspect only the current
repo and user-named repos; do not scan the whole machine.

Reuse known facts. Infer product name from the repo and language from the
conversation. Recommend a spec folder (`specs/` in the current repo if none
exists), a handoff file there, the detected test commands, and the existing
implementer or a sub-agent. One repo may hold both specs and code: use one repo
ID with `home: true`, and use that same ID for its components and dispatch.

## 2. Ask only what is missing

Show a short proposed setup in plain language: product, spec location, code
locations, framework/test commands and report language. Ask the user to confirm
or correct it. Folder creation in this proposal is authorized by confirmation.
Ask subsequent questions only for unresolved choices that affect setup; combine
related facts, and explain recommendations. Never ask for JSON keys, lock files,
search commands or machine mappings the agent can derive.

A new product can start with specs only. Unknown hardware, test commands or
product rules are recorded explicitly as pending, with the step that needs them;
"none" means confirmed not applicable, not unknown. Do not fabricate test commands,
mark unrun tests passing, or disable a capability to make a check green. Use the
installed Python and available text search tool; no new dependency installation.

## 3. Generate or complete the configuration

Use the templates in `${CLAUDE_PLUGIN_ROOT}/templates/`:

| Template | Destination |
|---|---|
| `specflow.md` | `<spec folder>/specflow.md` |
| `CLAUDE.docs.md`, `AGENTS.md` | `<spec folder>/CLAUDE.md`, `<spec folder>/AGENTS.md` |
| `decisions.md`, `deferred.md`, `couplings.md`, `gates-ext.md` | Same names in the spec folder |
| `workflow.json`, `components.json` | Same names in the spec folder |
| `scenarios.md` | `<spec folder>/T-int.md`; also `T-e2e.md` if that layer applies |
| `settings.project.json` | `.claude/settings.json` at each distinct repo root |
| `CLAUDE.code.md` | `CLAUDE.md` at code repo roots, except where it would collide with the spec instructions |

Do not copy `spec.md`, manual acceptance or review templates; consuming skills
read them from the plugin. Do not create code repos or run git init, commit, push
or publish as a side effect of setup. A non-git folder can receive draft settings,
but report Git initialization as a blocker for dispatch and handoff.

- Fill new files from confirmed/detected facts, including all actual paths and
  the installed `core` version. Use stable repo IDs without spaces or colons.
  Resolve the integration branch; if ambiguous, ask. Decisions use stable IDs.
- In `workflow.json`, include the home repo and confirmed code repos; set
  `capabilities.dispatch.repos` to the code repo IDs. With specs only, include
  just home and leave that list empty; report dispatch unavailable until code
  exists regardless of what a generic capability probe reports.
- Set `handoff.file` relative to the home repo. Set component states from product
  settings and repo integration branches consistently. New registries start
  empty; never change existing component records or status by hand.
- Populate product instructions from existing conventions. Keep hard rules.
  Remove example table rows in new decisions/couplings/deferred/scenario files
  when no entries exist. Fill optional extensions with "none yet"; list genuinely
  unknown requirements as pending in a short Setup pending section of
  `specflow.md`, with the stage they block. Do not pretend pending work is N/A.
- Fill known test commands in both product settings and implementer instructions.
  Unknown commands remain pending and block the affected G-T/G4/dispatch step.
  In a single repo, reference the canonical spec-folder implementer rules; dispatch
  creates its workspace copy rather than init replacing an existing root AGENTS.md.

### Existing files and repeated init

Read before changing. Keep unrelated settings, comments and user decisions.
Complete missing keys or template placeholders only within the confirmed setup.
For `.claude/settings.json`, merge just the specflow marketplace/plugin entries,
using the installed version as the pin; retain other plugins and settings. An
explicitly disabled plugin, conflicting pin/marketplace, non-placeholder differing
value, invalid JSON or conflicting instruction needs a focused question before
replacement. Never reset a populated registry or overwrite local paths.

Append `/specflow.local.json` once to the home repo's `.gitignore`, preserving
existing content. Verify with `git check-ignore`; if a later negation defeats
it, fix the scoped ignore entry and check again. If the mapping is already
tracked, stop before writing machine paths and explain that it must be untracked;
do not silently remove it from Git. In an existing instruction file, append a
small clearly marked specflow pointer only if compatible; avoid duplicate blocks.
Re-running init with the same answers must leave equivalent files unchanged.

## 4. Finish setup yourself

`<python>` is the working interpreter recorded in `specflow.md`. Run from the
installed plugin; quote paths, including spaces and non-ASCII characters.

1. Run `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/workflow_config.py" --product <spec folder> regen`.
2. Write the missing entries of `specflow.local.json` at the home repo root with
   `schema_version: 1` and `repos: {<id>: {"path": <absolute repo root>}}` for each
   known repo. Keep existing entries and never commit this file. The generated
   example stays portable; actual machine paths belong only in the local file.
3. If no lock exists, run `workflow_config.py --product <spec folder> lock-write`.
   Existing lock: run `lock-check`; a mismatch needs the user's upgrade decision
   before rewriting the pin/lock. Never fabricate installed plugin metadata or
   use a source clone to bypass installed-copy validation.
4. Run `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/workflow_config.py" --product <spec folder> validate`,
   then the same script with `doctor --for spec_work`. Also run
   `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/registry.py" --product <spec folder> check`;
   a damaged registry is not repaired by resetting components or states.
   With code repos and all dispatch prerequisites settled, also run `dispatch-check`.
   Scan generated setup/instruction files for unresolved placeholders; classify
   each pending item by the stage it blocks. Read command outputs and exit codes;
   a zero exit alone does not clear warnings or prove product tests work.
   Claim specification readiness only after the required configuration, lock,
   registry and spec_work checks pass with no unresolved setup warning. Pending
   product decisions must explicitly name the later stage they block. Dispatch
   readiness additionally requires a code repo and all its prerequisites; config
   probes alone do not make missing product tests or rules ready.
5. Repair mechanical setup problems within the confirmed scope and rerun only
   affected checks. For missing tools/permissions/plugins, ask the focused question
   or report the exact blocker; do not dump raw setup commands as the normal flow.

## 5. Hand back one result

Reply in the product's language, briefly:

- Ready for specifications / Setup incomplete, with the reason and check evidence.
- Spec folder and code repo locations; a compact count of created/updated/unchanged
  files (details only for conflicts).
- Pending product decisions and the stage they block; dispatch readiness separately.
- One next action: write the first spec, or answer the one blocking question.

Do not claim published/resumable or on-device verification. Initial publication is
handled by wrapup when requested; a handoff need not be published to begin specs.

| Excuse | Reality |
|---|---|
| The templates exist, so setup is done | Generate local mappings and a real lock, then run validation and doctor |
| Existing settings cannot be touched, so ask the user to merge JSON | Preserve unrelated keys and merge the confirmed specflow entries yourself |
| Unknown tests mean none | Record pending and block the stage that needs them |
| Re-init should regenerate everything | Preserve real choices, registries, locks and local paths; ask on conflicts |
