#include "heater.h"

static int target = 60;

int heater_set_target(int t)
{
    if (t < 30 || t >= 90) {
        return HEATER_ERR_RANGE;
    }
    target = t;
    return HEATER_OK;
}

int heater_get_target(void)
{
    return target;
}
