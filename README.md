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

Before drafting, `spec-write` defines the consumer/problem, observable success,
scope and open decisions. It asks only material unanswered questions; settled
requests proceed directly. Requests spanning independently testable capabilities
get component responsibilities and dependency order, using the existing registry.
G2-12 checks the definition in scope without reopening unrelated legacy sections.

G5 also records five quality axes (correctness, readability/simplicity,
architecture, security and performance) and a simplification assessment. Optional
advice cannot block conforming work; rule/contract violations and concrete defects
still block. A reviewer proposes simplifications read-only, including "no useful
simplification" when appropriate. An implementer edit needs fresh G4 and G5;
host tests alone do not prove timing, ISR or resource-ownership equivalence.

```
discuss → decide → write spec → G2 → scenarios first → G-T → prepare workspace → implement → G4 → G5 → close out → on-device and manual
                   spec-write   spec-review  tests-first  tests-review  [------------ dispatch ------------]      accept
                                                                        accept  code-review-gate
any time: change (a decision changed)    now and then: retro (something a gate let through)
```

Fourteen skills:

| Skill | When |
|---|---|
| `init` | Interactive setup: detect, ask, configure and verify; resumes incomplete setup |
| `init-product` | The same setup workflow; original command remains supported |
| `spec-write` | Writing or changing a component spec |
| `spec-review` | Spec review (G2) |
| `tests-first` | Before a dispatch: write the integration / end-to-end scenarios that become complete with it |
| `tests-review` | Test review (G-T) |
| `dispatch` | Dispatch: prepare workspace → implement → send back → close out |
| `accept` | Machine acceptance (G4) when the implementer reports done; the on-device stage |
| `code-review-gate` | Code review (G5): check table plus style table |
| `change` | A decision, requirement, hardware, contract or scenario changed |
| `pickup` | Picking up work at the start of a session: syncs the repos, then lists the handoff's open items in plain words with one recommendation; an item's links are read once you pick it. With a `handoff` block in `workflow.json` it also verifies the handoff and reports its level |
| `wrapup` | Wrapping up a session: rewrites the handoff whole with the current state only (one line plus a link per open item, at most 5120 bytes). With a `handoff` block in `workflow.json` it also exports the handoff manifest and verifies that it is published |
| `retro` | Looking back: what a gate let through, split into core or product |
| `deep-review` | The five-stage review method (used inside G2 and G5): a spec → code track and a code → risk track, an evidence check against counter-evidence, a coverage audit; `scripts/review_report.py` checks the findings file |

## 2. Install

In Claude Code, run once:

```text
/plugin marketplace add anthropics/claude-plugins-official
/plugin marketplace add forrestchang/andrej-karpathy-skills
/plugin marketplace add Ouip/qb-specflow
/plugin install specflow@specflow
```

Then open your project and run:

```text
/specflow:init
```

Init detects the project, proposes settings, asks only for missing decisions,
creates the configuration and runs its checks. You do not need to edit JSON,
copy a local path file, or run lock/doctor commands yourself. Existing users can
still use `/specflow:init-product`; both names run the same setup.

## 3. Setup: answer init, then start

1. Confirm or correct the detected project, spec location and code repos.
2. Answer any remaining questions about framework, tests and product rules.
   A specs-only project can start now; hardware and test decisions needed later
   are recorded as pending.
3. Init reports whether specifications can begin, whether dispatch is ready,
   and any specific blocker. Start with `/specflow:spec-write` when ready.

Re-run `/specflow:init` to finish an interrupted setup or configure another
machine's paths. It preserves existing choices, component states and local
mappings, merges its settings without replacing other plugins, and asks about
conflicts. It does not commit, push or publish your project.

The generated `specflow.md` holds product rules; `workflow.json` holds repo and
capability policy; `components.json` holds component state. Each machine's
absolute paths live only in the ignored `specflow.local.json`; the portable
example and `workflow.lock.json` can be versioned. Technical commands and their
limits are in section 9; they are run by init during ordinary setup.

Setup readiness is not test acceptance or a published handoff. To transfer work,
use `/specflow:wrapup`; another machine uses `/specflow:init` for its local mapping
and `/specflow:pickup` to verify the handoff. Only the reported `resumable` level
means that referenced work can resume there. Missing tools or plugin locks are
reported as blockers, never bypassed or claimed as passing.

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
5. With a `workflow.lock.json`: run `workflow_config.py --product <spec folder> lock-write` again from the updated plugin and commit the lock with `specflow.md`; until then `dispatch-check` refuses (exit 3).

At session start the SessionStart hook compares `core:` with the installed plugin version: a differing major version asks you to read the migration notes first; a newer plugin says "update available"; a plugin older than the pin says to update the plugin first.

## 6. Language

Skills, gates and templates are in English. Skill descriptions also carry Traditional Chinese trigger phrases, so requests in either language find the right skill. The scripts accept both languages in specs and tables: `Verify` or `驗` columns, `[verify: …]` or `〔驗：…〕` markers, `untested:` or `不測：`, and `Side A`/`Side B` or `A 端`/`B 端`. Existing Chinese specs need no rewrite. In the handoff file and task notes, `handoff.py` accepts both marks `local only` and `僅本機`; a mark exempts only the reference it directly follows (blanks or one opening bracket in between, e.g. `` `build/r1/` (local only) ``).

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
| `workflow_config.py --product <spec folder> validate / doctor / dispatch-check / lock-check / lock-write / regen / repo-path` | `workflow.json` and `workflow.lock.json` match their schema; each tool and capability declared vs measured on this machine; whether a dispatch may start (lock matches the installed plugins, required capabilities measured ok); writes the lock and `specflow.local.example.json` | that a tool it found works for the task; anything about another machine |
| `registry.py --product <spec folder> show / check / set / add / repo / claims` | component states, what can be worked on next, the revision; structure, rule and evidence problems; refuses a status change without a rule or without the evidence the rule needs | that evidence is true or new: only that the file exists and its sha256 matches |
| `handoff.py --product <spec folder> new-id / export / verify / report` | the handoff level (`local` from export; `published` or `resumable` from `verify --fetch`) and every missing, stale or pending item; refuses a handoff file over 5120 bytes or one with a suspected secret | that the handed-off work is right; anything not referenced from the handoff file or the selected task notes |
| `dispatch_run.py --product <spec folder> check / run / cancel` | the gate result; one run's verdict from its own evidence (report against the schema, changed files against `--allow`/`--frozen`, the process tree stopped) in `result.json` and the exit code | that the code is right (G4 and G5 decide); ignored files and writes outside the worktree; it never merges a branch or removes a worktree |
| `state_io.py`, `process_runner.py` (used by the scripts above; no command line) | locks, revisions and atomic writes under `<git common dir>/specflow-runtime/`; claims held until a process tree is confirmed stopped; timeout and cancel stop the whole tree | anything across machines (a local lock cannot stop another machine); a process started inside WSL that detaches itself |

Operational requirements: `handoff.local_only` prefixes apply to every repo;
list only paths no repo tracks. Component changes go through `registry.py`;
after rework, use a new evidence file at a new path (hash checks do not establish
freshness). Evidence declared human-written is produced by a person, never filled
in by a model. Trust a dispatch's `result.json` only with its process exit code;
G4 uses that run's worktree and recorded start SHA. A person merges its run branch
and removes its worktree; setup and dispatch do neither automatically.

File formats are in each script's docstring. Nothing runs a command from a map or value file unless the command line says so (`--run`, `--mutate`, `--allow`, `--cpp`). `doctor`, `dispatch-check` and `dispatch_run.py run` run the tools `workflow.json` names (version probes; the implementer). Exit codes of the workflow scripts: 0 pass, 1 refused or unexpected, 2 input or schema, 3 conflict or mismatch, 4 missing, 5 capability missing, 6 timeout.

Tests: `python -m unittest discover -s plugins/specflow/tests`. Tests that need a C compiler or preprocessor are skipped without one; set `SPECFLOW_TEST_CPP` or `SPECFLOW_TEST_CC` to run them.

## Verified environments

(Filled in after the first release.)

## License

MIT, see [LICENSE](LICENSE).
