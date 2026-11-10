# Local review benchmark

Measures how well a review (normally `specflow:deep-review`) finds known bugs and avoids false alarms. It runs by hand on one machine; nothing here runs in CI or calls a model by itself. `scripts/bench.py` checks and reproduces the ground truth and scores finished runs offline.

## Size

7 cases: 4 bug, 2 clean, 1 complex; 6 known bugs. The target is 20 bug, 20 clean and 10 complex cases. Cases are added only with ground truth that can be reproduced or that a person has reviewed; none are made up to reach the target. Scores from 7 cases have wide error bars: one case moves recall by 20 points.

| Case | Kind | What it tests |
|---|---|---|
| `bug-boundary-range` | bug | a spec rule's included upper bound rejected (`>=` for `>`); Track A |
| `bug-trust-length` | bug | a length byte from the UART used for `memcpy` unchecked; the spec is silent, so only Track B finds it |
| `bug-cross-file-units` | bug | seconds passed to a function that takes milliseconds, across two files |
| `bug-isr-race` | bug | a read-modify-write shared between an interrupt and the main loop |
| `clean-guarded-length` | clean | an unchecked `memcpy` whose only caller already drops long frames: an upstream guard |
| `clean-goto-cleanup` | clean | early exits that look like leaks but share one cleanup label; `strcpy` after a length check |
| `complex-sensor-failure` | complex | a sensor error swallowed in one module, so a third module keeps the heater on; a truncating division that moves the switch-on point |

## Layout

```
cases/<case id>/input/       what the reviewer sees: spec/ and src/
cases/<case id>/truth.json   ground truth; never shown to the reviewer
cases/<case id>/repro/       harness that shows the bug (or the absence of one); never shown to the reviewer
prompt.md                    the review prompt; its first line is the prompt_version
runs/<run id>/               one run: meta.json, judgements.json, <case id>.findings.json
```

`truth.json`: `kind` (bug, clean, complex), `units` (files a full review reads), `bugs` (`id`, `file`, `line_range`, `trigger`, `root_cause`, `impact`), `traps` for clean cases (what looks wrong and where the guard is), `provenance` (`method`: reproduced, exercised or human_reviewed; `human_review`; `repro`: `cflags`, `expect_exit`, `expect_output`, optional `run_prefix`).

## Ground truth

- A bug case counts only when its harness reproduces it: `python scripts/bench.py repro bench/cases --cc gcc` builds `input/src/*.c` with `repro/*.c` under AddressSanitizer, UndefinedBehaviorSanitizer or ThreadSanitizer, runs it, and compares the exit code and output with `truth.json`. A harness that exits with the expected code but without the expected output is MISMATCH, never REPRODUCED.
- A clean case is EXERCISED when its harness drives the suspicious path under the sanitizers and finds nothing. That is evidence for the input range the harness covers, not a proof; `human_review` stays `pending` until a person signs it off.
- Last reproduction: 2026-11-09, WSL Ubuntu 24.04, gcc 13.3.0: all 7 cases REPRODUCED or EXERCISED. `bug-isr-race` needs Linux (`setarch -R`); on Windows without gcc, `repro` prints `compiler gcc not found`.

## Running a review round

1. Create `runs/<run id>/meta.json`:
   ```json
   {"run_id": "2026-11-09-a", "run_index": 1, "model": "<model id>", "effort": "<effort>",
    "prompt_version": "bench-prompt-1", "commit_sha": "<git rev-parse HEAD>", "source": "working_tree",
    "tools": ["Read", "Grep", "Bash"], "notes": ""}
   ```
   `source` is `commit` when the cases are committed at `commit_sha` (evidence is then read from that commit), otherwise `working_tree`.
2. For each case, copy `cases/<id>/input/` to an empty temporary folder and give the reviewer `prompt.md` with that folder. Each case in a fresh session or sub-agent.
3. The reviewer writes `runs/<run id>/<case id>.findings.json` (format: `scripts/review_report.py`).
4. A person judges every CONFIRMED finding in `runs/<run id>/judgements.json`:
   ```json
   {"judged_by": "<name>", "date": "2026-11-09",
    "matches": {"<case id>": {"<finding_id>": {"bug": "<bug id> | null | NEW",
                                               "trigger": true, "root_cause": true, "impact": true, "note": ""}}}}
   ```
   A finding matches a bug only when its trigger, root cause and impact all match the truth. Pointing at the right lines or using the right words is not enough. `NEW` marks a real defect missing from the ground truth; it counts as neither TP nor FP, and the case's truth must be updated before the next round.
5. `python scripts/bench.py score bench/cases bench/runs/<run id>`. For stability, repeat the round (run_index 2, 3) with the same model, effort and prompt version and score all runs together.

## Metrics

Only CONFIRMED findings count as reported defects.

- **Precision** = TP / (TP + FP). TP: a CONFIRMED finding judged to match a known bug on trigger, root cause and impact (a second finding on the same bug is a duplicate, counted in neither). FP: any other CONFIRMED finding, including partial matches.
- **Recall** = TP / (TP + FN) = known bugs matched / known bugs in the cases run.
- **False-positive rate**: the negative unit is a clean case. Rate = clean cases with at least one FP / clean cases run.
- **Evidence validity** = findings that pass the `review_report.py` checks (file, line range, quote, commit, execution records) / all findings.
- **Coverage** = mean over cases of the `units` the report lists under `coverage.reviewed` / all `units`.
- **Stability** = cases with the same matched bugs and the same has-FP outcome in every run / cases in 2 or more runs. Needs 2 or more runs with the same model, effort and prompt version.

Precision, recall and false-positive rate print "not computed" while any CONFIRMED finding has no judgement. Every score is for the listed cases only.

## Recorded runs

None yet. A run is recorded only with its meta.json, findings files and a person's judgements; no score is written here without them.

A smoke run on 2026-11-09 checked that the pipeline works end to end (sub-agents following `deep-review`, `review_report.py`, `bench.py score`). It is not recorded as a result: the same model wrote the cases and judged the findings, and it ran once. It found a defect the ground truth lacked (`complex-sensor-failure/2`), which was then reproduced and added.
