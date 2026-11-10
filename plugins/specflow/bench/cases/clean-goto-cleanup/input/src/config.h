#ifndef CONFIG_H
#define CONFIG_H

#define CONFIG_OK  0
#define CONFIG_ERR (-1)

struct config {
    char key[16];
    char value[32];
};

/* text must not be NULL */
int config_load(const char *text, struct config *out);

#endif
