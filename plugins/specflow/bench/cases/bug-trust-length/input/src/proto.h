#ifndef PROTO_H
#define PROTO_H

#define PROTO_PAYLOAD_MAX 16

void proto_on_frame(const unsigned char *frame);
const unsigned char *proto_payload(void);
unsigned char proto_payload_len(void);

#endif
