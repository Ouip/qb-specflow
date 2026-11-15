# Interactive init

User requirement: after plugin installation, run init and answer questions; the
agent completes setup instead of handing the user template edits and commands.

Implementation: keep existing Python interfaces, make `init-product` own
discovery, scoped configuration merging, local paths, lock and verification.
Add `init` as a thin command alias. Simplify README's install/setup sections.
This is a skill/document change, not a new deterministic installer executable.

Baseline document walkthrough found four gaps: discoverable commands were asked
again, existing settings blocked automatic completion, partial reruns could not
finish, and specs-only/lock failures lacked explicit readiness outcomes.

- [x] Inspect current init, templates and configuration-script interfaces.
- [x] Record pre-edit document baseline with a read-only agent.
- [x] Rewrite interactive setup, preserving existing values and conflicts.
- [x] Add thin init entry point and simplify installation/setup documentation.
- [x] Verify template checks and focused existing configuration/smoke tests.
- [x] Review original four cases plus same-repo layout and tracked local mapping.

Verification: 68 configuration tests and 5 portable smoke tests passed (73 total).
After adding the registry readiness check, 34 registry tests passed too (107 total).
Template checks and diff whitespace checks passed. Generic lint exited 0 with
the existing unreadable ignored-cache warning. These exercise existing tool
interfaces and fixtures, not a real plugin installation or model compliance.

Review corrections: retain operational evidence/dispatch caveats in README §9,
remove a stale no-new-skill statement, and run registry check during init so a
damaged components file cannot be reported as dispatch-ready. The document
walkthrough covered discovery, partial rerun, specs-only, unavailable installed
lock, same-repo layout, tracked local mapping and conflicting settings.

No new dependency, release, commit, push or user-project setup is part of this
change. Specs-only readiness does not authorize dispatch or claim test coverage.
