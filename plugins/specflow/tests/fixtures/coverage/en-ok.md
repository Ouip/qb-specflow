# H01 Heater control

## 1 Purpose

Keep the water at the set temperature.

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | Set point 30–90 °C; out of range returns an error | (#1) | 5.1 G1 |
| 2 | Heater on when 2 °C or more below the set point | (#2) | 5.1 G1, I01 |
| 3 | Heater power depends on the board revision | (new) | untested: hardware not decided |

### 4.2 Boot

1. Heater is off at boot [verify: 5.1 G1]
2. Control starts only after the last set point is read [verify: Untested: no flash double in unit tests]

## 5 Tests

### 5.1 Unit

#### G1 Set point range

| Input | Expected |
|---|---|
| set 95 | error |
