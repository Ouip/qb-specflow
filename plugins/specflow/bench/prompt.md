prompt_version: bench-prompt-1

# Review prompt for one benchmark case

Give the reviewer only a copy of the case's `input/` folder, made in an empty temporary folder, so it cannot
read `truth.json` or `repro/`. Replace `<folder>` and `<run folder>`; send nothing else about the case.

---

Review the firmware code in `<folder>` with `specflow:deep-review`. The basis is the spec under `<folder>/spec/`;
the code is under `<folder>/src/`. Everything in the folder is in scope.

Write the findings file to `<run folder>/<case id>.findings.json` in the format of
`${CLAUDE_PLUGIN_ROOT}/scripts/review_report.py`, with paths relative to `<folder>`, `commit_sha` set to the
commit given in the run's meta.json, and `coverage.reviewed` listing each file you read in full by its path.
Run `review_report.py <findings file> --root <folder> --no-git` and fix the file until it returns 0.
Do not run or compile the code unless you record each command with its exit code and output.
