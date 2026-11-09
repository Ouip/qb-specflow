# P02 Frame protocol

## 3 Interface

### 3.1 Frame

A frame from the UART is `[0x7E][len][payload: len bytes][crc8]`. The UART driver calls
`proto_on_frame(frame)` once per complete frame. `proto_payload()` and `proto_payload_len()` give the
last payload to the application.
