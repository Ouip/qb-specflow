/* Stubs the I2C driver and the heater output.
 * #2: a reading of raw 929 (58.0625 °C, 1.9375 °C below 60 °C) must leave the heater off (T07 4.1#1).
 * #1: one good reading of 20 °C, then the bus fails; the heater must turn off (T07 4.1#2). */
#include <stdint.h>
#include <stdio.h>

void ctrl_step(void);

static int bus_ok = 1;
static uint16_t reading;
static int heater;

int i2c_read16(uint8_t addr, uint16_t *out)
{
    (void)addr;
    if (!bus_ok) {
        return -1;
    }
    *out = reading;
    return 0;
}

void heater_set(int on)
{
    heater = on;
}

int main(void)
{
    int bugs = 0;

    reading = 929;
    ctrl_step();
    if (heater) {
        puts("BUG REPRODUCED (#2): heater on at 1.9375 C below the target (T07 4.1#1)");
        bugs++;
    }
    reading = 20 * 16;
    ctrl_step();
    bus_ok = 0;
    ctrl_step();
    if (heater) {
        puts("BUG REPRODUCED (#1): sensor unreadable but the heater stays on (T07 4.1#2)");
        bugs++;
    }
    if (!bugs) {
        puts("no bug");
    }
    return bugs ? 1 : 0;
}
