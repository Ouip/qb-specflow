# CHANGELOG

## 0.1.0 — 2026-11-13

From a product (2026-11-13): its workflow fixes were product scripts: a handoff checked between machines, component status changed only by rule, a gate before a dispatch, success judged only from evidence. Their generic parts move into the core, so any product gets them through its own settings; the product keeps only its settings and, if it has its own commands, a thin adapter.

- New scripts (stdlib only, `--product <spec folder>`; formats in each docstring): `workflow_config.py` (policy, lock, this machine's paths, doctor, dispatch gate), `registry.py` (the only way to change `components.json`), `handoff.py` with `handoff.manifest.schema.json` (export, verify, report, new-id), `dispatch_run.py` (gate, then an implementer tool in its own worktree, judged from evidence), and the libraries `state_io.py` and `process_runner.py`. Tests are in `tests/`.
- New optional files in the spec folder: `workflow.json` (policy), `workflow.lock.json` (written by `lock-write`), `components.json` (component status, deps, blocked); and `specflow.local.json` at the spec repo root (this machine's paths, never committed).
- `pickup`: with a `handoff` block in `workflow.json`, runs `handoff.py verify --fetch --json` after reading the handoff file; the reply adds the handoff ID, the level in the product's language and every problem entry. Only `resumable` is "can resume".
- `wrapup`: with a `handoff` block, code repos are pushed first, then `new-id` on the handoff file's first line, `export`, the commit of `commit_paths`, push, `verify --fetch`. Only `published: true` is "published". The existing rules (at most 5120 bytes, only your own paths, never `git add -A`, other sessions' files untouched) are unchanged.
- `dispatch`: with `workflow.json`, `dispatch-check` before starting; with `dispatch.implementer`, the implementer runs through `dispatch_run.py run` and `result.json` counts only together with its exit code; with `components.json` and `workflow.json`, every state change goes through `registry.py set`. Nothing merges a run branch or removes a worktree automatically.
- `accept`: G4 for a `dispatch_run.py` run uses the run's worktree and start SHA; a record used as human evidence is written by a person, never by the model.
- `init-product`: also copies `workflow.json` and `components.json`, creates `.gitignore` with `specflow.local.json` when there is none, and lists `regen`, `lock-write` and `doctor` as next steps.
- README §3 is now the guide for a new project joining; §9 lists the new scripts; §6 lists the marks `local only` and `僅本機`.
- Known limits: evidence freshness is not checked (after a rework, use a new path); a git worktree is not an OS sandbox; a process started inside WSL that detaches itself is not stopped from Windows.

### Template changes

- New: `workflow.json`, `components.json`.
- `specflow.md`: `core: 0.1.0`.
- `settings.project.json`: `ref` `v0.1.0`.

### Format changes

None. The new files are optional: a product without `workflow.json` gets the same skill behavior as 0.0.3. To use the tools, copy `templates/workflow.json` and `templates/components.json` into the spec folder, add `specflow.local.json` to the spec repo's `.gitignore`, and follow README §3 from step 3.

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
