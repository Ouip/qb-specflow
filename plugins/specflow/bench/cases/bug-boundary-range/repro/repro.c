#include <stdio.h>
#include "heater.h"

int main(void)
{
    if (heater_set_target(90) != HEATER_OK || heater_get_target() != 90) {
        puts("BUG REPRODUCED: 90 is rejected although H01 4.1#1 includes it");
        return 1;
    }
    puts("no bug");
    return 0;
}
