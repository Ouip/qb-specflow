#ifndef SENSOR_H
#define SENSOR_H

#include <stdint.h>

#define SENSOR_OK  0
#define SENSOR_ERR (-1)

int sensor_read_raw(int16_t *raw);

#endif
