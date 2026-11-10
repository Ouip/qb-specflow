/* Host side of the link. */
#define LINK_TIMEOUT_MS 500
#define LINK_ACK_MS     500   /* waits this long for an ack */
#define LINK_WINDOW_S   1
#define LINK_TICK_PERIOD 10
#define HOST_FRAME_W    320

int link_send(const char *buf, int len) { return len; }
