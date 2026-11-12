# specflow

A spec-driven development workflow for firmware products, packaged as a Claude Code plugin. The core defines only the mechanism: steps, gate checklists, templates and check scripts. The chip, framework, test commands and product-specific gate items come from each product's own `specflow.md` and extension files.

## 1. What it is

Three mechanisms:

| Mechanism | What it catches | Where |
|---|---|---|
| Rule → Verify mapping | Rules a spec states in section 4 that no test verifies and the code does not follow | the Verify column in spec section 4; `scripts/coverage.py` (every reference resolves); `scripts/behavior.py` (the test runs, and fails when the rule is broken); the G5 check table |
| Couplings table | One side changes and the other side (a component linked physically or by data flow) does not follow | `templates/couplings.md`; `scripts/couplings.py` (references; `--values` reads and compares the values per variant); required by G2 and `change` |
| Change impact scan | A decision changes and only the first spec gets updated | the seven steps and the impact list of `/specflow:change` |

Four gates (checklists in `gates/`): G2 spec review, G-T test review, G4 machine acceptance, G5 code review.

```
discuss → decide → write spec → G2 → scenarios first → G-T → prepare workspace → implement → G4 → G5 → close out → on-device and manual
                   spec-write   spec-review  tests-first  tests-review  [------------ dispatch ------------]      accept
                                                                        accept  code-review-gate
any time: change (a decision changed)    now and then: retro (something a gate let through)
```

Thirteen skills:

| Skill | When |
|---|---|
| `init-product` | A product joins the workflow for the first time |
| `spec-write` | Writing or changing a component spec |
| `spec-review` | Spec review (G2) |
| `tests-first` | Before a dispatch: write the integration / end-to-end scenarios that become complete with it |
| `tests-review` | Test review (G-T) |
| `dispatch` | Dispatch: prepare workspace → implement → send back → close out |
| `accept` | Machine acceptance (G4) when the implementer reports done; the on-device stage |
| `code-review-gate` | Code review (G5): check table plus style table |
| `change` | A decision, requirement, hardware, contract or scenario changed |
| `pickup` | Picking up work at the start of a session: syncs the repos, then lists the handoff's open items in plain words with one recommendation; an item's links are read once you pick it |
| `wrapup` | Wrapping up a session: rewrites the handoff whole with the current state only (one line plus a link per open item, at most 5120 bytes) |
| `retro` | Looking back: what a gate let through, split into core or product |
| `deep-review` | The five-stage review method (used inside G2 and G5): a spec → code track and a code → risk track, an evidence check against counter-evidence, a coverage audit; `scripts/review_report.py` checks the findings file |

## 2. Install

1. Add the marketplaces this plugin depends on, then this one, and install it:
   ```
   claude plugin marketplace add anthropics/claude-plugins-official
   claude plugin marketplace add forrestchang/andrej-karpathy-skills
   claude plugin marketplace add Ouip/qb-specflow
   claude plugin install specflow@specflow
   ```
2. Write the product repo's `.claude/settings.json` from `templates/settings.project.json` (`/specflow:init-product` generates it); `ref` pins a tag. Trust the folder when Claude Code asks.
3. `claude plugin list` shows `specflow@specflow` as enabled.

## 3. Set up a product

1. `/specflow:init-product` creates `specflow.md`, the templates and `.claude/settings.json`; it never overwrites an existing file.
2. Fill every `[FILL]` in `specflow.md`, including `Language` (the language for reports, the handoff file and replies).
3. Write the product's gate items in the gate-extension file (`gates-ext.md`).
4. Write the first spec with `/specflow:spec-write`.

## 4. Tune

- What a product may change: `specflow.md`, the gate-extension file, the templates it copied, and its own skills (registered under "Extra steps" in `specflow.md`).
- Gate checklists can be extended, never shortened.
- Core skills cannot be overridden. To change the core, open an issue.

## 5. Update

1. Read the CHANGELOG sections between your version and the new one, especially "Template changes" and "Format changes".
2. Change `ref` in the product's `.claude/settings.json` to the new tag (e.g. `v0.0.3`). The tag must exist on the repo: a release is tagged `v<version>`.
3. Refresh the marketplace and update the plugin:
   ```
   claude plugin marketplace update specflow
   claude plugin update specflow@specflow
   ```
   then `/reload-plugins` in a running session.
4. Change `core:` in `specflow.md` to the new version, and apply any template changes the CHANGELOG lists.

At session start the SessionStart hook compares `core:` with the installed plugin version: a differing major version asks you to read the migration notes first; a newer plugin says "update available"; a plugin older than the pin says to update the plugin first.

## 6. Language

Skills, gates and templates are in English. Skill descriptions also carry Traditional Chinese trigger phrases, so requests in either language find the right skill. The scripts accept both languages in specs and tables: `Verify` or `驗` columns, `[verify: …]` or `〔驗：…〕` markers, `untested:` or `不測：`, and `Side A`/`Side B` or `A 端`/`B 端`. Existing Chinese specs need no rewrite.

## 7. Third-party dependencies and licenses

Dependencies are not copied; they are declared under `dependencies` in `plugins/specflow/.claude-plugin/plugin.json`. The list and credits are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## 8. Terms and an example

| Term | In one sentence |
|---|---|
| Spec | A component's seven-section document; section 4 holds the behavior rules, each with an ID, a source and a Verify cell |
| Verify cell | How a rule is verified: `5.1 G<n>`, a scenario ID, `5.5` (manual), or `untested: <reason>` |
| Contract | The full text of an interface (e.g. a frozen C header); only a person can release a new version |
| Coupling | Settings of two components directly linked physically or by data flow; when one side changes, the other must follow |
| Variant | A version of the same firmware on different hardware: dev board / product board, different panels, different part numbers |
| Decisions table | One row per user decision: ID, content, what it supersedes |
| Deferred list | Things a gate judged "not fixing this time", with when each will be handled |
| Sub-agent | A Claude dispatched by the main session, with its own context |

Example (a hypothetical product): a single-board thermostat timer. STM32G0, FreeRTOS, no radio, a seven-segment display, three buttons, an NTC, one heater relay; the only test port is UART; the implementer is a sub-agent.

| To fill | How |
|---|---|
| `specflow.md` | framework = CMake + arm-none-eabi-gcc; test layers = unit (Unity on the host, `ctest -L unit`), integration (core linked with plain gcc), end-to-end (a UART script driving the real board), manual (brightness, relay click, temperature measurement); impact search = `rg`; lint = `cppcheck`, `clang-format --dry-run` |
| `AGENTS.md` | change only your own module, never `include/` or `tests/uart/`; no `stm32*.h` or `FreeRTOS.h` in `core/` |
| Gate extensions | G2: every relay safe-state rule (off on power loss) has a source; G4: `arm-none-eabi-size` within flash; G5: no non-ISR RTOS API called from an ISR |
| Couplings table | NTC divider resistor ↔ lookup-table constants ↔ display decimals; system clock ↔ tick period ↔ every `*_MS` constant; pin table ↔ PCB revision |

What stays as is: every skill's steps, the check-table format, the couplings columns, the deferred list, the decisions table, the seven-section spec, the manual acceptance format.

## 9. Check scripts and the review benchmark

| Script | What it shows | What it does not show |
|---|---|---|
| `coverage.py <spec> --catalog …` | every rule's Verify reference resolves; how many rules it checked; a `warning:` line for section-4 writing it cannot check (`--strict` fails on it); `--changed-since <ref>` skips a spec whose section 4 did not change | that any test exercises the rule |
| `behavior.py <spec> --map <verify map> [--run/--mutate --workdir … --allow …] [--strict]` | per rule: `MUTATION_VERIFIED`, `TEST_PASSED`, `TEST_FAILED`, `MANUAL_VERIFIED`, `WAIVED`, `REFERENCE_ONLY`, `UNVERIFIED`, `BLOCKED` | anything beyond the mutations the map lists; a mutation that only breaks the build or times out is never counted |
| `couplings.py <table> --specs … --code …` | every reference in Side A and Side B exists | that the two sides agree |
| `couplings.py … --values <value file> [--cpp <preprocessor>]` | the two sides' values per variant, units converted: `PASS`, `FAIL`, `UNVERIFIED`, `BLOCKED`, `MANUAL` | protocols, struct layouts, timing order, API meaning (listed as `MANUAL`); macros the text strategy cannot evaluate (`UNVERIFIED`) |
| `review_report.py <findings.json> --root <dir>` | each finding's file, lines, quotes, commit and execution records are traceable | that any finding is a real bug |
| `bench.py cases / repro / score` | benchmark size; ground truth reproduced with sanitizers; precision, recall, false-positive rate, evidence validity, coverage, stability of judged runs | any score without judged runs ([bench/README.md](plugins/specflow/bench/README.md)) |

File formats are in each script's docstring. Nothing runs a command from a map or value file unless the command line says so (`--run`, `--mutate`, `--allow`, `--cpp`).

Tests: `python -m unittest discover -s plugins/specflow/tests`. Tests that need a C compiler or preprocessor are skipped without one; set `SPECFLOW_TEST_CPP` or `SPECFLOW_TEST_CC` to run them.

## Verified environments

(Filled in after the first release.)

## License

MIT, see [LICENSE](LICENSE).
