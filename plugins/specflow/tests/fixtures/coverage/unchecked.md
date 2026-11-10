# H01 Heater control

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | Set point 30–90 °C; out of range returns an error | (#1) | 5.1 G1 |

### 4.2 Limits

| Rule | Verify |
|---|---|
| Heater off above 95 °C | 5.1 G1 |

### 4.3 Boot

- Heater is off at boot
- Control starts after the set point is read
  - an indented note is not counted

## 5 Tests

### 5.1 Unit

#### G1 Set point range

| Input | Expected |
|---|---|
| set 95 | error |
