# Implementer rules

For the implementer (sub-agent or external tool). Copied into the workspace together with the spec at dispatch.

## Hard rules

1. Touch only the paths allowed in spec section 7, that is, your own component. [FILL: exceptions]
2. Do not change a single character of the contract. If you think the contract is wrong, write it in the report and stop.
3. The pure-logic layer ([FILL: e.g. `core/`]) does not touch the framework or hardware: no framework or hardware headers, no clock reads, no logging.

## How to test

[FILL: one command per test layer, the same as "Test layers and commands" in `specflow.md`]

## Common done criteria

- Every line of spec section 6 passes.
- Every test layer's command returns 0.
- The report follows the format ([FILL: path of the format file]); anything not done goes under unmet items, never reported as done.

## No faked tests

1. Do not loosen thresholds, delete assertions or comment out cases to go green.
2. Do not change frozen tests or scenarios.
3. Do not detect the test environment to take another path, or hard-code values to make a test pass.
4. Do not report a test you never ran as passing; do not claim on-device verification with host-only evidence.

## Style

[FILL: tag each rule [tool] (checked by the formatter) or [G5] (checked rule by rule in code review)]

## When stuck

- The spec is unclear or contradicts itself: do not guess; write it in the report notes and stop.
- The same error survives three fixes: stop and report what you tried and the output.
- You need to change a file outside the allowed paths: stop and report; do not change it yourself.
