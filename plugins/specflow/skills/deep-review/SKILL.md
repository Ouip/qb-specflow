---
name: deep-review
description: Use when reviewing code, a diff/PR/branch, a spec or design document, or code against a spec, and the user named no other review skill (「審查」「review」「檢查有沒有問題」「找 bug」「對照規格」).
---

# deep-review

Every conclusion traces back to the source text. Finding problems and judging them are separate passes; whatever cannot be judged is Unverified.

## Size

- **Small** (1–3 files, or within 300 lines): do all five stages yourself; stage 3 still reopens the sources.
- **Large**: stage 1 cuts the scope into units. Stage 2 dispatches one sub-agent per unit; stage 3 is done by agents that took no part in stage 2. At most 3 agents at once. Use Workflow only when the user asks. Work-card format at the end.

## Stage 1: Scope

1. List what is under review: each file's path and line count. For a diff, include the callers of the files it touches.
2. List the basis: specs, frozen headers, decisions table, project rules (AGENTS.md, CLAUDE.md), requirements the user stated. First write down the priority order for when bases conflict; if it cannot be set, ask the user.
3. Read every section of every basis that bears on the targets.
4. Write the scope table: what is reviewed, and what is not (with reasons).

Done when: every target file is in the scope table; every relevant section of every basis has been read.

## Stage 2: Find problems independently

1. Read file by file, line by line; take every unit through each review dimension.
2. Each finding records: location (`file:line`), problem, basis text (quoted verbatim), trigger (a concrete input or state, and the wrong result it leads to), severity.
3. Record suspicious items you are unsure of too, marked "to check", for stage 3 to judge.

Done when: every "reviewed" file in the scope table is read; every dimension of every unit has a conclusion: a finding, or one line "checked, no problem".

## Stage 3: Check the evidence

For each finding:

1. Reopen the code and the basis; the source text decides; stage 2's paraphrase is only a lead.
2. Walk the trigger through the code. Verify whatever can be verified by running tests, a small script, or grep.
3. Look for counter-evidence: handled elsewhere, an exception in the basis, already known from an earlier review.
4. Judge:
   - **Confirmed**: the source text and the walk-through both support it.
   - **Unverified**: needs hardware, a runtime environment, or more evidence. Say how to verify it.
   - **Rejected**: the counter-evidence holds. Say why.

Done when: every finding has a judgment and a one-line reason; every Confirmed one quotes the source.

## Stage 4: Coverage audit

1. Against the scope table, list unread files and uncovered dimensions.
2. Check the seams between units: things each side only half-reviewed when the scope was cut, e.g. upstream and downstream of a data flow, caller and callee, linked hardware settings.
3. List unresolved risks: things known only on hardware, things depending on a third party's internals, things the basis itself does not define.
4. Small gaps go back to stages 2 and 3; what cannot be covered goes to Unreviewed.

Done when: every gap is "covered" or "listed in Unreviewed, with the reason".

## Stage 5: Report

In this order:

1. One-sentence conclusion: how many in each of the four classes, and the most severe one.
2. Scope and basis: what was reviewed, and which version of the basis.
3. **Confirmed**: by severity. Each gives location, problem, trigger, basis text, suggestion.
4. **Unverified**: each says how to verify it.
5. **Rejected**: one line of reason each, kept so it is not reported again.
6. **Unreviewed**: unreviewed scope and why, plus unresolved risks.

Done when: every stage-2 finding is in Confirmed, Unverified or Rejected; the three counts add up to the total number of findings.

## Severity

- **High**: following the basis, the user sees a wrong result, or a hang, crash, data corruption or security problem.
- **Medium**: the basis itself is wrong, or the code and the basis disagree but the basis looks wrong; a person must decide.
- **Low**: structure, style or project rules that do not affect behavior.

## Review dimensions

- **Correctness**: per the basis, does a concrete input give a wrong result.
- **Error paths**: failure, timeout, disconnect, re-entry, power loss midway; every path is closed out.
- **Boundaries and combinations**: max, min, negative, empty and longest-string values the basis does not list; state combinations the basis does not cover (B happens while A is in progress).
- **The basis itself**: does the basis contradict itself; does it agree with headers and comments; did later decisions reach every affected file.
- **Couplings**: two components directly linked physically or by data flow; do their settings agree, e.g. size, format, timing, orientation, timeout, units.
- **External behavior**: assumptions the basis or code makes about drivers, libraries and hardware, checked against their source or datasheets.
- **Deferred items**: things earlier reviews rated low, marked "fix next time" or worked around; are they still there.
- **Blind spots of machine checks**: places grep, the build and tests check only literally, e.g. indirect calls, things that hold only on a clean build, things that compile only through an indirect dependency.
- **Tests**: tests really test the behavior; every item of the basis has a matching test.
- **Style**: project rules, one by one.

## Work cards (large scale)

- **A finding card** gives: scope (file list), basis paths and their order when they conflict, the review dimensions (the whole section pasted), the finding format, the output file path, read only, done criteria. Have it write the output file as it goes; after compaction it reads the output file first and continues.
- **A checking card** gives: the finding list, basis paths, stage 3's four steps and three judgments. Checking is done by agents that took no part in finding.
