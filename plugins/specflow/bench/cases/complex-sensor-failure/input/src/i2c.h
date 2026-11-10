#ifndef I2C_H
#define I2C_H

#include <stdint.h>

/* Board I2C driver (not in this review). Returns 0 on success, -1 on a bus error or NACK. */
int i2c_read16(uint8_t addr, uint16_t *out);

#endif
