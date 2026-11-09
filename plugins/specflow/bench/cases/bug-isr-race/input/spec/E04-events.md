# E04 Events

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | Every received byte produces exactly one event; no event is lost or duplicated | (#5) | 5.1 G1 |
| 2 | `uart_rx_isr()` runs in interrupt context; `app_take_event()` runs in the main loop | (#5) | untested: needs hardware |
