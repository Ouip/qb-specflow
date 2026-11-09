# CHANGELOG

## Unreleased

Fewer false passes and unchecked review claims. Existing commands, specs and tables behave as before.

- `behavior.py` (new): tells "a Verify reference exists" apart from "a test verifies the rule". Reads a verify map (JSON) and gives each rule `MUTATION_VERIFIED`, `TEST_PASSED`, `TEST_FAILED`, `MANUAL_VERIFIED`, `WAIVED`, `REFERENCE_ONLY`, `UNVERIFIED` or `BLOCKED`. Commands run only with `--run`/`--mutate`, `--workdir` and `--allow`, as argument lists with a timeout; `--mutate` works in a temporary copy and counts a mutation only when the expected assertion fails. `--strict` fails rules whose `require` is not met.
- `couplings.py --values` (new option): reads both sides' values (`#define` as text, or through a gcc-compatible preprocessor with `--cpp` per build variant), converts units and compares; `PASS`, `FAIL`, `UNVERIFIED`, `BLOCKED`, `MANUAL`. Unknown units, unreadable symbols and undecided variants are never `PASS`. No `eval()`; a restricted expression reader (`values.py`).
- `coverage.py`: `scan()` can also return each rule's Verify value; output and exit codes unchanged. The docstring says it checks references only.
- `deep-review`: stage 2 runs a spec → code track and a code → risk track; stage 3 verifies each finding against counter-evidence with the statuses `CONFIRMED`, `NEEDS_VERIFICATION` (was Unverified), `REJECTED`; stage 4 sorts scope into `REVIEWED`, `NOT_REVIEWED`, `INSUFFICIENT_EVIDENCE`; stage 5 writes a findings file. The report states whether independent sub-agents ran.
- `review_report.py` (new): checks a findings file for traceability (file at the reviewed commit, line range, verbatim quotes, execution records, required fields). It does not judge whether a finding is right.
- `bench.py` and `bench/` (new): 7 local benchmark cases (4 bug, 2 clean, 1 complex; 6 known bugs) with reproducible ground truth, and offline scoring of judged review runs. No CI.
- G2-10, G2-11, G5-1 and the G5 check table name the new scripts and statuses; `code-review-gate`, `spec-review` and `spec-write` point at them.

### Template changes

- `couplings.md`: "a tool check" names `couplings.py --values`.

### Format changes

None required. New optional files: verify map (`behavior.py`), value file (`values.py`), findings file (`review_report.py`).

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
