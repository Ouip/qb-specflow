# Couplings

| # | Side A | Side B | Shared quantity | How kept consistent | Last checked |
|---|---|---|---|---|---|
| 1 | L05 3.1, `host/link.c`, `LINK_TIMEOUT_MS` | `device/include/cfg.h`, `DEV_TIMEOUT_MS` | link timeout | tool check | — |
| 2 | `host/link.c`, `LINK_ACK_MS` | `device/include/cfg.h`, `DEV_ACK_MS` | ack wait | tool check | — |
| 3 | `host/link.c`, `LINK_WINDOW_S` | `device/include/cfg.h`, `DEV_WINDOW_MS` | retry window | tool check | — |
| 4 | `host/link.c` | `device/include/cfg.h`, `DEV_TIMEOUT_MS` | removed constant | tool check | — |
| 5 | `host/link.c`, `LINK_TICK_PERIOD` | `device/include/cfg.h`, `DEV_TICK_MS` | tick | tool check | — |
| 6 | `host/link.c`, `HOST_FRAME_W` | `device/include/board.h`, `FRAME_W` | frame width | tool check | — |
| 7 | L05 3.2 | `device/include/frame.h` | frame layout | manual comparison in spec review | — |
