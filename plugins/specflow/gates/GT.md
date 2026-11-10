# G-T test review (integration and end-to-end scenarios; never whoever wrote the test)

Generic items. Product items (e.g. which outputs end-to-end tests can observe) are in the gate-extension file named in `specflow.md`.

| Item | Check | Passes when |
|---|---|---|
| GT-1 | The code tests exactly its row in the catalog | Settings and input sequence match "Input"; every item of "Expected" has an assertion |
| GT-2 | Nothing loosened, nothing extra or missing, no dependence on run order | Compared item by item with the catalog, no extra or missing assertions; it passes when run alone |
| GT-3 | End-to-end assertions use only what is observable | Only the device's external outputs (screen, commands, connections, fixture reports); no internal state |
| GT-4 | Frozen | Reviewed scenario code is frozen; the implementer may not change it; a scenario believed wrong goes in the report for the main session to judge |
