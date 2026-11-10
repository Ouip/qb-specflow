# T07 Thermostat

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | `ctrl_step()` runs every 100 ms: reads the temperature and turns the heater on when it is 2 °C or more below the target | (#7) | 5.1 G1 |
| 2 | When the sensor cannot be read, the heater is turned off within one step | (#7) | 5.1 G2 |
| 3 | The sensor reports 1/16 °C per count | datasheet p.12 | 5.1 G1 |
