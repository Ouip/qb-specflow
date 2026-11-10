---
name: deep-review
description: Use when reviewing code, a diff/PR/branch, a spec or design document, or code against a spec, and the user named no other review skill (「審查」「review」「檢查有沒有問題」「找 bug」「對照規格」).
---

# deep-review

Every conclusion traces back to the source text. Finding problems and judging them are separate passes; whatever cannot be judged is `NEEDS_VERIFICATION`. Problems are found on two independent tracks: **Track A** (spec → code) misses what the spec never says, **Track B** (code → risk) misses what only the spec knows, so both run.

## Size

- **Small** (1–3 files, or within 300 lines): do all five stages yourself; stage 3 still reopens the sources. Write `"context": "single_context"` in the findings file and say in the report that the two tracks and the check shared one context.
- **Large**: stage 1 cuts the scope into units. Stage 2 dispatches sub-agents per unit, Track A and Track B in separate sub-agents; stage 3 is done by agents that took no part in stage 2. At most 3 agents at once. Use Workflow only when the user asks. Work-card format at the end. Write `"context": "independent_subagents"` only when separate sub-agents really ran; if none could be started, do it yourself as `single_context` and say so. Never describe a pass as done by an independent agent when it was not.

## Stage 1: Scope

1. List what is under review: each file's path and line count. For a diff, include the callers of the files it touches.
2. Record the reviewed version: `git rev-parse HEAD`. Uncommitted changes in scope → say so; line numbers then refer to the working tree.
3. List the entry points Track B starts from: changed or public functions, interrupt and callback handlers, message and command handlers, init and shutdown paths.
4. List the basis: specs, frozen headers, decisions table, project rules (AGENTS.md, CLAUDE.md), requirements the user stated. First write down the priority order for when bases conflict; if it cannot be set, ask the user.
5. Read every section of every basis that bears on the targets.
6. Write the scope table: what is reviewed, and what is not (with reasons).

Done when: every target file is in the scope table; the commit and entry points are listed; every relevant section of every basis has been read.

## Stage 2: Find problems on two tracks

The tracks do not read each other's findings until stage 3.

**Track A: spec → code.** For each in-scope requirement, rule (section 4), interface, decision and test row:

1. Find the code that implements it and read it. Write down the `file:line` ranges actually read; a rule counts as checked only with those ranges, never because its table row exists.
2. Check: implemented at all; correct for the concrete inputs the rule names; contract versions, types and constants match the basis verbatim; the test named in its Verify cell exercises it (`behavior.py` status when the product keeps a verify map).

**Track B: code → risk**, whether or not the basis mentions the risk. From each stage-1 entry point, follow callers, callees, error paths and external interfaces. At every step go through:

- **Boundaries**: max, min, zero, negative, empty, longest, off by one.
- **Error handling**: every failure return checked; timeout, disconnect, power loss midway; every path closed out.
- **Resource lifetime**: allocate and free, open and close, locks taken and released on every path, use after free, stale pointers.
- **Concurrency and re-entrancy**: shared data between tasks and interrupts, read-modify-write, volatile, ordering, a callback that re-enters.
- **Input validation and trust boundaries**: lengths, indexes and IDs from outside (UART, radio, files, other boards) checked before use.
- **Data consistency**: units, scaling, sign, endianness, two copies of one value, state updated halfway.
- **Compatibility**: stored formats, protocol versions, older peers and configs.
- **Undocumented assumptions**: things the code relies on that no basis, comment or assert states.

"The spec says nothing" is not a bug. A Track B finding without a basis needs a concrete trigger and a concrete wrong result; otherwise it is a question for the user.

Each finding records: location (`file:line`), problem, track, basis text quoted verbatim (or "none: Track B"), trigger (a concrete input or state, and the wrong result it leads to), severity. Record suspicious items you are unsure of too, marked "to check", for stage 3 to judge.

Done when: every "reviewed" file in the scope table is read; every Track A item has its read ranges; every entry point has been through every Track B dimension; each dimension of each unit has a conclusion: a finding, or one line "checked, no problem".

## Stage 3: Verify each finding

For each finding, as the verifier:

1. Reopen the code and the basis yourself; the source text decides; stage 2's paraphrase is only a lead.
2. Walk the trigger through the code: the path from the entry point to the faulty line, step by step with `file:line`. Verify whatever can be verified by running tests, a small script, or grep, and record each command with its exit code and an output excerpt.
3. Search for counter-evidence before believing it: validation upstream (callers, the protocol layer), middleware or framework guarantees, configuration and build flags, platform invariants (single core, interrupt priorities, alignment), other guards (watchdog, retries, asserts), an exception in the basis, the deferred list and earlier reviews. Write down where you looked and what you found.
4. Judge:
   - `CONFIRMED`: a traceable path from an entry point, a trigger that can really occur, a concrete impact, the source quoted, and the counter-evidence search found nothing that stops it.
   - `NEEDS_VERIFICATION`: needs hardware, a runtime environment, or more evidence. Say what would settle it.
   - `REJECTED`: the counter-evidence holds. Say where.

Never write "reproduced", "tested" or "ran" for something you did not execute in this review; reading the code is `read` or `trace`.

Done when: every finding has a judgment and a one-line reason; every `CONFIRMED` one quotes the source and lists the counter-evidence checked.

## Stage 4: Coverage audit

1. Against the scope table, sort every file, entry point and Track B dimension into `REVIEWED`, `NOT_REVIEWED` (with the reason) or `INSUFFICIENT_EVIDENCE` (read, but a conclusion needs something not available: hardware, a third party's source, a missing spec).
2. Check the seams between units: things each side only half-reviewed when the scope was cut, e.g. upstream and downstream of a data flow, caller and callee, linked hardware settings.
3. List unresolved risks: things known only on hardware, things depending on a third party's internals, things the basis itself does not define.
4. Small gaps go back to stages 2 and 3; what cannot be covered stays `NOT_REVIEWED` or `INSUFFICIENT_EVIDENCE`.

No finding in an area is not proof the area is correct. The report says what was not reviewed instead of implying it is clean.

Done when: every gap is `REVIEWED`, `NOT_REVIEWED` with the reason, or `INSUFFICIENT_EVIDENCE` with the reason.

## Stage 5: Report

The report, in this order:

1. One-sentence conclusion: how many in each class, and the most severe one. State `single_context` or `independent_subagents`.
2. Scope and basis: what was reviewed, the commit, and which version of the basis.
3. **Confirmed**: by severity. Each gives location, problem, trigger, basis text, suggestion.
4. **Needs verification**: each says how to verify it.
5. **Rejected**: one line of reason each, kept so it is not reported again.
6. **Coverage**: the `NOT_REVIEWED` and `INSUFFICIENT_EVIDENCE` lists with reasons, plus unresolved risks.

Next to the report, write the findings file (`<report name>.findings.json`, format in `${CLAUDE_PLUGIN_ROOT}/scripts/review_report.py`) with one record per finding and the coverage lists, then run `<python> ${CLAUDE_PLUGIN_ROOT}/scripts/review_report.py <findings file> --root <reviewed folder>` (add `--no-git` for uncommitted code) and paste its output. It checks only that files, line ranges, quotes, the commit and execution records are traceable; it does not show that any finding is a real bug. Fix the findings file until it returns 0.

### Finding record

| Field | Content |
|---|---|
| `finding_id` | `DR-<n>`, stable within the review |
| `status` | `CONFIRMED`, `NEEDS_VERIFICATION` or `REJECTED` |
| `severity` | High, Medium or Low |
| `track` | `spec_to_code` or `code_to_risk` |
| `commit_sha` | the reviewed commit |
| `file`, `line_range` | where the problem is, relative to the reviewed folder; `<n>` or `<n>-<m>` |
| `trigger` | the concrete input or state |
| `expected_behavior` | per the basis, or per the stated risk for Track B |
| `actual_behavior` | what the code does instead |
| `impact` | what the user or system sees |
| `evidence` | `file`, `line_range` and a verbatim `quote` from the reviewed version |
| `verification_method` | from read, grep, trace, test, script, build, hardware; the last four need `executions` (command, exit code, output excerpt), and so does `"reproduced": true` |
| `verification_limitations` | what this verification could not show; "none" only if nothing |
| `counterevidence_checked` | each place searched for a guard, and what was there |

Done when: every stage-2 finding is in Confirmed, Needs verification or Rejected; the three counts add up to the total number of findings; `review_report.py` returns 0.

## Severity

- **High**: following the basis, the user sees a wrong result, or a hang, crash, data corruption or security problem.
- **Medium**: the basis itself is wrong, or the code and the basis disagree but the basis looks wrong; a person must decide.
- **Low**: structure, style or project rules that do not affect behavior.

## Review dimensions

Track B's list above, plus:

- **Correctness**: per the basis, does a concrete input give a wrong result.
- **Combinations**: state combinations the basis does not cover (B happens while A is in progress).
- **The basis itself**: does the basis contradict itself; does it agree with headers and comments; did later decisions reach every affected file.
- **Couplings**: two components directly linked physically or by data flow; do their settings agree, e.g. size, format, timing, orientation, timeout, units (`couplings.py --values` reads and compares the values it can).
- **External behavior**: assumptions the basis or code makes about drivers, libraries and hardware, checked against their source or datasheets.
- **Deferred items**: things earlier reviews rated low, marked "fix next time" or worked around; are they still there.
- **Blind spots of machine checks**: places grep, the build and tests check only literally, e.g. indirect calls, things that hold only on a clean build, things that compile only through an indirect dependency.
- **Tests**: tests really test the behavior; every item of the basis has a matching test. A test that exists and passes is not yet a test that fails when the rule breaks.
- **Style**: project rules, one by one.

## Work cards (large scale)

- **A finding card** is for one track: scope (file list), entry points (Track B), basis paths and their order when they conflict (Track A), the track's steps and dimensions (the whole section pasted), the finding format, the output file path, read only, done criteria. Have it write the output file as it goes; after compaction it reads the output file first and continues.
- **A checking card** gives: the finding list (location, claim, trigger only, not the finder's reasoning), basis paths, stage 3's four steps and three judgments, the finding-record table. Checking is done by agents that took no part in finding.

| Excuse | Reality |
|---|---|
| The spec does not mention it, so there is nothing to check | Track B checks risks the spec never names |
| The spec does not mention it, so it is a bug | Without a concrete trigger and impact it is a question, not a finding |
| No guard in this function, so it is Confirmed | Search callers, framework, config and platform first (stage 3 step 3) |
| I walked through it in my head, so it is reproduced | Only an executed command with its output is a reproduction |
| No findings in this file, so it is fine | Say whether it was `REVIEWED`, `NOT_REVIEWED` or `INSUFFICIENT_EVIDENCE` |
