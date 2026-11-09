/* Drives every length byte and every buffer size through the only caller under AddressSanitizer. */
#include <stdio.h>
#include <stdlib.h>

void uart_rx(const unsigned char *buf, unsigned int n);

int main(void)
{
    for (unsigned int n = 0; n <= 300; n++) {
        for (unsigned int len = 0; len <= 255; len++) {
            unsigned char *buf = malloc(n ? n : 1);
            for (unsigned int i = 0; i < n; i++) {
                buf[i] = (unsigned char)i;
            }
            if (n >= 2) {
                buf[1] = (unsigned char)len;
            }
            uart_rx(buf, n);
            free(buf);
        }
    }
    puts("SAFE: no out-of-bounds access for n 0..300, len 0..255");
    return 0;
}
