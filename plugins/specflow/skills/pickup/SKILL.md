---
name: pickup
description: Use at the start of a session to pick up work, continue the previous session, or start from the handoff file (「接手」「開工」).
---

# Pick up

1. Read `specflow.md` in the product's spec folder (missing, or no `format: 2` in its header → ask the user to run `/specflow:init-product` first). The handoff file path is in it.
2. Read the handoff file: where things stand, the next step, what awaits the user.
3. For the spec repo and every code repo in `specflow.md`: `git status --short`, `git fetch`, `git status -sb`, `git branch -vv`. List every uncommitted file and leave it alone: it may belong to another session.
4. Sync: clean working tree and only behind → `git pull --ff-only`. Not clean, or diverged → do not pull; report how many commits behind.
5. The branches and worktrees the handoff mentions still exist.

Reply: the handoff's next step, pending decisions, each repo's `git status -sb` verbatim, uncommitted files. Start no work until the user says so.

| Excuse | Reality |
|---|---|
| The handoff file describes the state, so report that | The handoff is from the last wrap-up; check the working tree now and list the uncommitted files yourself |
