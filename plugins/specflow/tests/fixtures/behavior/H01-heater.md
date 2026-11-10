# H01 Heater

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | Target 30–90 °C; outside the range returns `ERR_RANGE` | (#1) | 5.1 G1 |
| 2 | Heater turns on when actual is 2 °C or more below target | (#2) | 5.1 G2 |
| 3 | Heater power depends on the board revision | (new) | untested: board revision not decided |
| 4 | Heater turns off when the sensor cannot be read | (new) | 5.5 |
| 5 | Timer reaching 0 turns the heater off | (#3) | 5.1 G3 |
| 6 | Display shows the target | (#4) | 5.1 G1 |

## 5 Tests

### 5.1 Unit

#### G1 Target range

#### G2 Heater threshold

#### G3 Timer

### 5.5 Manual

| Step | Expected | If not, check first |
|---|---|---|
| Unplug the sensor | Heater LED off | wiring |
