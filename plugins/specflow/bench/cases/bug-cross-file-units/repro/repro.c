#include <stdio.h>
#include "timer.h"

void link_send(void);

int main(void)
{
    timer_set_now(1000);
    link_send();
    if (timer_deadline_ms() != 1000 + 2000) {
        printf("BUG REPRODUCED: deadline %u ms, expected %u ms\n", timer_deadline_ms(), 1000u + 2000u);
        return 1;
    }
    puts("no bug");
    return 0;
}
