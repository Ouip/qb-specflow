#include <stdio.h>
#include <string.h>
#include "proto.h"

int main(void)
{
    unsigned char frame[2 + 255 + 1];
    memset(frame, 0xAA, sizeof frame);
    frame[0] = 0x7E;
    frame[1] = 40; /* a corrupted or hostile length byte */
    puts("sending len 40 into a 16-byte payload buffer");
    fflush(stdout);
    proto_on_frame(frame); /* AddressSanitizer reports global-buffer-overflow here */
    puts("no bug");
    return 0;
}
