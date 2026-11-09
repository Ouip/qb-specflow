#include <string.h>
#include "frame.h"

static unsigned char store[FRAME_STORE_MAX];
static unsigned int stored;

void frame_store(const unsigned char *p, unsigned int len)
{
    memcpy(store, p, len);
    stored = len;
}

unsigned int frame_len(void)
{
    return stored;
}
