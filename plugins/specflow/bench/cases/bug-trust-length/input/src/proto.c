#include <string.h>
#include "proto.h"

static unsigned char payload[PROTO_PAYLOAD_MAX];
static unsigned char payload_len;

void proto_on_frame(const unsigned char *frame)
{
    unsigned char len = frame[1];

    memcpy(payload, &frame[2], len);
    payload_len = len;
}

const unsigned char *proto_payload(void)
{
    return payload;
}

unsigned char proto_payload_len(void)
{
    return payload_len;
}
