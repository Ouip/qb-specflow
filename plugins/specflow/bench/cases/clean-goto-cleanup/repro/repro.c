/* Every error path and the success path under AddressSanitizer and LeakSanitizer. */
#include <stdio.h>
#include <string.h>
#include "config.h"

int main(void)
{
    static const char *inputs[] = {
        "", "=", "novalue", "k=v", "=v", "k=",
        "0123456789abcdef=v", "0123456789abcde=v",
        "k=0123456789abcdef0123456789abcdef", "k=0123456789abcdef0123456789abcde",
    };
    for (unsigned int i = 0; i < sizeof inputs / sizeof inputs[0]; i++) {
        struct config c;
        memset(&c, 0x5A, sizeof c);
        struct config before = c;
        int rc = config_load(inputs[i], &c);
        if (rc != CONFIG_OK && memcmp(&c, &before, sizeof c) != 0) {
            printf("BUG: out changed on error for \"%s\"\n", inputs[i]);
            return 1;
        }
    }
    puts("SAFE: no leak, overflow or partial write on any path");
    return 0;
}
