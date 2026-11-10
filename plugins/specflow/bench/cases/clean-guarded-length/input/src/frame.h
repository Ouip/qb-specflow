#ifndef FRAME_H
#define FRAME_H

#define FRAME_STORE_MAX 16

/* Stores one payload. Callers pass len <= FRAME_STORE_MAX. */
void frame_store(const unsigned char *p, unsigned int len);
unsigned int frame_len(void);

#endif
