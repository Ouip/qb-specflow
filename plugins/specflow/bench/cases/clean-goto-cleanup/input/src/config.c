#include <stdlib.h>
#include <string.h>
#include "config.h"

int config_load(const char *text, struct config *out)
{
    int rc = CONFIG_ERR;
    char *eq;
    char *copy = malloc(strlen(text) + 1);

    if (copy == NULL) {
        return CONFIG_ERR;
    }
    strcpy(copy, text);

    eq = strchr(copy, '=');
    if (eq == NULL) {
        goto out;
    }
    *eq = '\0';
    if (strlen(copy) >= sizeof out->key || strlen(eq + 1) >= sizeof out->value) {
        goto out;
    }
    strcpy(out->key, copy);
    strcpy(out->value, eq + 1);
    rc = CONFIG_OK;
out:
    free(copy);
    return rc;
}
