---
name: wrapup
description: Use when wrapping up, switching sessions, or leaving a handoff for the next session (「收工」「換 session」「交接」).
---

# Wrap up

Right after a merge with dispatch close-out unfinished, the user's "close out" (「收尾」) means stage 4 of `/specflow:dispatch`; if unsure, ask one question.

When the spec folder has a `workflow.json` with a `"handoff"` block, steps 3–5 run as in "With a handoff block" below. Without one, that section does not apply.

1. Read `specflow.md` in the product's spec folder (missing, or no `format: 2` in its header → ask the user to run `/specflow:init-product` first).
2. `git status --short` in each repo; split into two piles: changed by this session, and not. Leave the other pile (staged files included) exactly as it is: no edit, delete, revert, add or commit.
3. Rewrite the handoff file whole: it holds the current state only; history lives in git.
   - Where things stand, what awaits the user, files left by other sessions.
   - Open items in suggested order, one plain-words line each (what gets done, who does it) plus a link to where its details live: a spec section, the deferred list, a review record, or a detail file next to the handoff. Delete a detail file when its item is done.
   - Carry every open item of the old handoff, from all its sections, before overwriting it.
   - At most 5120 bytes. Over → move details into linked files; every open item keeps its line.
4. Commit only your own paths (the handoff file counts) with `git commit -- <paths…>`; others' staged files stay staged. Never `git add -A`, `git add .` or `git commit -a`.
5. Push every branch with new commits. Rejected → no rebase, no force; stop and paste the rejected output.
6. "Extra steps" wrap-up in `specflow.md`: "none" → skip; a skill → invoke it; not found → write "skipped, `/x:y` not found".
7. After `git fetch`, run `git status -sb` in each repo.

## With a handoff block

Every rule of steps 2–5 still holds; only the order changes, and the scripts check the handoff. `<handoff.py>` stands for `<python> "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" --product <spec folder>`, with `<python>` from the `python:` header line of `specflow.md`.

a. Code repos first: commit your own paths there (step 4) and push (step 5); a branch without an upstream: `git push -u <remote> <branch>`.
b. `<handoff.py> new-id` prints a handoff ID.
c. Rewrite the handoff file (step 3) with that ID as its first line. A reference to something that stays on this machine on purpose is directly followed by the mark `local only` (or `僅本機`).
d. In the spec repo: `git add -- <your own paths>`, including the handoff file, task notes and new files the handoff needs.
e. `<handoff.py> export --task <id>… --handoff-id <ID> --json`: one `--task` per component this session worked on; add `--ref <repo id>=<branch>` for work on a branch the mapped checkout does not have checked out (e.g. a dispatch worktree). By exit code:
   - 0 → f.
   - 4 with `level` `local` → f; the reply says "saved locally, missing:" and lists every entry of `missing` and `pending_transfers`.
   - 4 with `level` null → content the handoff needs is uncommitted or untracked (`pending_transfers`). Your own file: commit it (a) or add it (d), then once more from b with a new ID. Another session's file, or the second try → stop and paste those entries.
   - 3 → the ID is taken or something conflicts: once more from b with a new ID; 3 again → stop and paste the output.
   - 2 or anything else → stop and paste the output; its `error` names the cause (a suspected secret, a handoff file over 5120 bytes, bad input). Nothing was written.
f. `git add -- <commit_paths>`, then `git commit -- <your own paths> <commit_paths>` (`commit_paths` from the export JSON).
g. Push the spec repo (step 5).
h. `<handoff.py> verify --fetch --json`. Only `published: true` here may be called "published" (exit 0, or 5 when a capability is missing on this machine). Otherwise report the export's level and every entry of `missing`, `stale`, `local_dirty` and `timed_out` verbatim.

Then steps 6 and 7. Level words, in the product's language: `local` = saved locally, `published` = published (e.g. 繁體中文 本地保存／已發布). Never "can resume": only pickup on the receiving machine decides that.

## Done when

- Step 7's output is pasted verbatim in the reply; no repo is ahead or behind.
- `wc -c <handoff file>` is pasted in the reply and shows at most 5120.
- Other sessions' files are listed in the reply, in the same state as before the wrap-up.
- If something cannot be done, say which step it stopped at.
- With a handoff block: the reply gives the handoff ID and the level, both from the script JSON only: "published" only after `published: true` in h; "saved locally" with every missing entry when the export gave `local`.

| Excuse | Reality |
|---|---|
| Committing the already-staged files too is tidier | Staged files are not necessarily yours; commit only your own paths |
| Appending a section for this session is quicker | An appended handoff grows every session and the next pickup reads all of it; rewrite it whole |
| The push succeeded, so the wrap-up is done | Paste `git status -sb` to show nothing is ahead or behind |
| An extra step that cannot be found need not be mentioned | Write "skipped, `/x:y` not found" so the user knows what was not done |
| The push returned 0, so the handoff is published | Only `published: true` from `verify --fetch` is "published"; a pushed handoff can still miss a file |
