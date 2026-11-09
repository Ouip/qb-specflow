#include "timer.h"

static unsigned int now;
static unsigned int deadline;

void timer_set_now(unsigned int now_ms)
{
    now = now_ms;
}

void timer_arm(unsigned int timeout_ms)
{
    deadline = now + timeout_ms;
}

unsigned int timer_deadline_ms(void)
{
    return deadline;
}
