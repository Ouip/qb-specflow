# C06 Config

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | `config_load(text, out)` parses `key=value`; `text` is never NULL | (#6) | 5.1 G1 |
| 2 | A line without `=`, a key of 16 bytes or more, or a value of 32 bytes or more returns `CONFIG_ERR` and leaves `out` unchanged | (#6) | 5.1 G1 |
| 3 | No path leaks memory | (#6) | 5.1 G1 |
