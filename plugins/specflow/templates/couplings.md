# Couplings

One row per place where two components share a quantity (size, clock, millisecond value, coordinate, format).
When either side changes, `/specflow:change` puts the whole opposite side on the impact list; spec review (G2-11) rereads the opposite side whenever it reviews either side.

| # | Side A | Side B | Shared quantity | How kept consistent | Last checked |
|---|---|---|---|---|---|
| 1 | [FILL] | [FILL] | [FILL] | [FILL] | — |

- Write Side A and Side B as references that can be found; `couplings.py` checks each one: spec sections as `<ID> <section>` (e.g. `X03 4.2`);
  symbols in backticks (e.g. `` `FRAME_W` ``); file paths in backticks, containing `/` (e.g. `` `src/qr/gen.py` ``).
- How kept consistent, one of four: a compile-time assert (best), a test row (a scenario or a 5.1 group), manual comparison in spec review, a tool check (e.g. `couplings.py --values`, which reads both sides' values, converts units and compares them per variant; the value-file format is in `scripts/values.py`).
- Last checked: the decision ID of the last time both sides were confirmed consistent; `—` if never.
- The table only catches registered couplings. When writing a spec shows that two components need the same value, add a row.

## Common firmware couplings (think these through when filling)

- Hardware variant ↔ driver settings (panel model, color order, orientation, mirroring, pins)
- Sensor frame ↔ display size, viewfinder, processing region
- Clock ↔ timeout constants; the ordering between timeouts
- Pin table ↔ board revision
- Protocol parameters between two boards ↔ both implementations
- Numbers in on-screen text ↔ constants in code
- Test-data generators ↔ production coordinates and formats
