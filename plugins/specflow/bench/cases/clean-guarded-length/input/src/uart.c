#include "frame.h"

void uart_rx(const unsigned char *buf, unsigned int n)
{
    unsigned int len;

    if (n < 2) {
        return;
    }
    len = buf[1];
    if (len > FRAME_STORE_MAX || len + 2 > n) {
        return; /* drop: too long, or shorter than it claims */
    }
    frame_store(&buf[2], len);
}
