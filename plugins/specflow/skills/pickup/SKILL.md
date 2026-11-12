---
name: pickup
description: Use at the start of a session to pick up work, continue the previous session, or start from the handoff file (「接手」「開工」).
---

# Pick up

1. Read only the lines of `specflow.md` (in the product's spec folder) that pickup needs, not the whole file:
   `grep -E '^format:|^\| (Language|Spec folder|Code repos|Handoff file) \|' specflow.md`
   (PowerShell: `Select-String -Pattern` with the same regex).
   Missing, or no `format: 2` → ask the user to run `/specflow:init-product` first.
2. For the spec repo and every code repo in `specflow.md`: `git status --short`, `git fetch`, `git status -sb`, `git branch -vv`. List every uncommitted file and leave it alone: it may belong to another session.
3. Sync: clean working tree and only behind → `git pull --ff-only`. Not clean, or diverged → do not pull; report how many commits behind.
4. Read the handoff file, after the sync so it is the latest. Leave its links unopened for now.
5. The branches and worktrees the handoff mentions still exist.

Reply in the product's language: each repo's `git status -sb` verbatim, uncommitted files, where things stand, then the handoff's open items as a numbered list in plain words. Each item says what gets done and who does it (the user, or Claude alone); internal codes and jargon go in brackets at most. End with one recommended item and why. Start work when the user picks an item; only then read that item's links.

| Excuse | Reality |
|---|---|
| The handoff file describes the state, so report that | The handoff is from the last wrap-up; check the working tree now and list the uncommitted files yourself |
| Reading every linked file first gives a better summary | Each handoff line is enough to list the item; read links only for the item the user picks |
