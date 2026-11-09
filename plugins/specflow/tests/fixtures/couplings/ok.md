# 連動表

| # | A 端 | B 端 | 共同的量 | 怎麼保持一致 | 最近核對 |
|---|---|---|---|---|---|
| 1 | 加熱 H01 3.1／4.2、`HEATER_PWM_HZ`、`HEATER_*` | 顯示 D02 3.1、`src/heater/heater.c`、`heater.c:2`、`tools/gen_table.py` | 頻率 | 編譯期斷言 | — |
