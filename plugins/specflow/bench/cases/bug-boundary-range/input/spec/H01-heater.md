# H01 Heater

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | `heater_set_target(t)` accepts 30–90 °C, both ends included; outside the range it returns `HEATER_ERR_RANGE` and keeps the old target | (#1) | 5.1 G1 |
| 2 | `heater_get_target()` returns the last accepted target; 60 °C after start | (#1) | 5.1 G1 |
