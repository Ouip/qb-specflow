#include "timer.h"

#define LINK_ACK_TIMEOUT_S 2

static int waiting;

void link_send(void)
{
    /* the frame is queued by the driver; wait for the ack */
    waiting = 1;
    timer_arm(LINK_ACK_TIMEOUT_S);
}

int link_waiting(void)
{
    return waiting;
}
