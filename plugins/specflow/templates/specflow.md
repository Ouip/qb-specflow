---
format: 2
core: 0.0.2
python: python
---
# specflow product settings

Every specflow skill reads this file first. Replace every `[FILL]`; write "none" where a field does not apply, never leave it blank.
Header lines: `format` is this file's format version; `core` is the core version the product uses (change it when upgrading); `python` is the interpreter for the core scripts.

| Field | Value |
|---|---|
| Product | [FILL] |
| Language | [FILL] (for reports, the handoff file and replies to the user, e.g. English, 繁體中文) |
| Spec folder | [FILL] (the folder holding this file) |
| Code repos | [FILL] (one entry per repo: name, path) |
| Chip and framework | [FILL] (for people; the core does not read it) |
| Component states | Not started → In progress → Awaiting integration → Awaiting hardware → Done (rename here if needed) |
| Contract form and extraction | C header; the ```c block after `<!-- header: path -->` (change if different) |
| Contract change rules | [FILL] (the file recording release steps and version history; "none" if none) |
| Implementer | [FILL] (sub-agent, or the full command of an external tool) |
| Impact search | [FILL] (one command for text, one for code, e.g. `rg -n "<regex>" <spec folder>`, `rg -n "<regex>" <code repo>`) |
| Consistency lint | [FILL] (one entry per command; all must return 0; "none" if none) |
| Scenario catalogs | [FILL] (paths of the integration and end-to-end scenario catalogs, for `coverage.py --catalog`) |
| Decisions table | [FILL] |
| Deferred list | [FILL] |
| Couplings table | [FILL] |
| Manual acceptance records | [FILL] (e.g. `<code repo>/docs/reviews/<id>-manual.md`) |
| Handoff file | [FILL] (the file sessions hand off through) |
| Gate-extension file | [FILL] (built from the core `templates/gates-ext.md`) |
| Extra steps | wrap-up: [FILL]; dispatch close-out: [FILL] (a product skill name, e.g. `/<product>-post-merge`; "none" if none) |
| Sub-agents | 1 per gate; ask the user before running more than 3 in parallel |

## Test layers and commands

| Layer | What it verifies | Command | Passes when |
|---|---|---|---|
| Unit | one component's pure logic | [FILL] | returns 0 |
| Integration | the pure logic of two or more components together | [FILL] | returns 0 |
| On-device end-to-end | real hardware; results visible on a screen, in commands or connections | [FILL] | returns 0 |
| Manual | judged only by eye or instrument | per spec 5.5 | every row of the manual acceptance record has a result |
