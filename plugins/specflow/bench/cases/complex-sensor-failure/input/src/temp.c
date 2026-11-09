#include "sensor.h"

static int16_t last_raw;

void temp_update(void)
{
    sensor_read_raw(&last_raw);
}

/* temperature in 0.1 °C */
int temp_celsius_x10(void)
{
    return last_raw * 10 / 16;
}
