#include "i2c.h"
#include "sensor.h"

#define SENSOR_ADDR 0x48

int sensor_read_raw(int16_t *raw)
{
    uint16_t v;

    if (i2c_read16(SENSOR_ADDR, &v) != 0) {
        return SENSOR_ERR;
    }
    *raw = (int16_t)v;
    return SENSOR_OK;
}
