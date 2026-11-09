# CHANGELOG

## 0.2.0 — 2026-11-09

Everything in English.

- 13 skills, 4 gate checklists and 15 templates rewritten in English. Skill descriptions start with "Use when" and carry Traditional Chinese trigger phrases.
- `coverage.py` and `couplings.py` accept both languages: `Verify`/`驗` columns, `[verify: …]`/`〔驗：…〕` markers, `untested:`/`不測：`, `Side A`/`Side B` and `A 端`/`B 端`. Messages are in English.
- The SessionStart hook and the check scripts print in English.
- `couplings.py --list` prints every reference of every row, one line per side; `/specflow:change` turns each line of an affected row into an impact-list row.
- `templates/settings.project.json` points at `Ouip/qb-specflow`.
- MIT license (`LICENSE`). The repo is public: installing needs no GitHub sign-in.

### Template changes

- Every template is in English; the fill-in marker is `[FILL]` (was `【填】`).
- `specflow.md` gains a `Language` field (reports, handoff, replies to the user).
- `spec.md`: section 4 columns `| # | Rule | Source | Verify |`; list rules end with `[verify: …]`; source marker `(new)`.
- `couplings.md`: columns `Side A` and `Side B`.
- `init-product` copies `decisions.md`, `deferred.md`, `couplings.md` and `gates-ext.md` under their own names.
- `deferred.md`: the `/specflow:change` step that sends unfixed merged code to the deferred list is step 7 (was misnumbered 6).

### Format changes

`specflow.md` header `format: 2`: field names are in English. Migrating from `format: 1`: copy the values into a fresh `templates/specflow.md` and add `Language`. No other product file needs to change; Chinese specs and tables still pass the scripts.

## 0.1.0 — 2026-11-09

First version, in Traditional Chinese.

- 13 skills: `init-product`, `spec-write`, `spec-review`, `tests-first`, `tests-review`, `dispatch`, `accept`, `code-review-gate`, `change`, `pickup`, `wrapup`, `retro`, `deep-review`.
- 4 gate checklists: `gates/G2.md`, `G4.md`, `G5.md`, `GT.md`.
- 15 templates (`templates/`).
- 3 scripts: `lint-generic.sh`, `coverage.py`, `couplings.py` (plus `check-templates.sh` and `test-version-check.sh` for development).
- 1 hook: SessionStart compares `core:` in the product's `specflow.md` with the plugin version.

### Template changes

First version.

### Format changes

`specflow.md` header `format: 1`.
