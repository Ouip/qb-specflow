# U05 UART frames

## 3 Interface

### 3.1 Frame

The UART driver calls `uart_rx(buf, n)` with the `n` bytes of one received frame:
`[0x7E][len][payload: len bytes]`. Payloads longer than 16 bytes are dropped.
