---
name: wrapup
description: Use when wrapping up, switching sessions, or leaving a handoff for the next session (「收工」「換 session」「交接」).
---

# Wrap up

Right after a merge with dispatch close-out unfinished, the user's "close out" (「收尾」) means stage 4 of `/specflow:dispatch`; if unsure, ask one question.

1. Read `specflow.md` in the product's spec folder (missing, or no `format: 2` in its header → ask the user to run `/specflow:init-product` first).
2. `git status --short` in each repo; split into two piles: changed by this session, and not. Leave the other pile (staged files included) exactly as it is: no edit, delete, revert, add or commit.
3. Handoff file: where things stand, the next step, what awaits the user, files left by other sessions.
4. Commit only your own paths (the handoff file counts) with `git commit -- <paths…>`; others' staged files stay staged. Never `git add -A`, `git add .` or `git commit -a`.
5. Push every branch with new commits. Rejected → no rebase, no force; stop and paste the rejected output.
6. "Extra steps" wrap-up in `specflow.md`: "none" → skip; a skill → invoke it; not found → write "skipped, `/x:y` not found".
7. After `git fetch`, run `git status -sb` in each repo.

## Done when

- Step 7's output is pasted verbatim in the reply; no repo is ahead or behind.
- Other sessions' files are listed in the reply, in the same state as before the wrap-up.
- If something cannot be done, say which step it stopped at.

| Excuse | Reality |
|---|---|
| Committing the already-staged files too is tidier | Staged files are not necessarily yours; commit only your own paths |
| The push succeeded, so the wrap-up is done | Paste `git status -sb` to show nothing is ahead or behind |
| An extra step that cannot be found need not be mentioned | Write "skipped, `/x:y` not found" so the user knows what was not done |
