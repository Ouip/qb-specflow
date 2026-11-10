#ifndef HEATER_H
#define HEATER_H

#define HEATER_OK        0
#define HEATER_ERR_RANGE (-1)

int heater_set_target(int t);
int heater_get_target(void);

#endif
