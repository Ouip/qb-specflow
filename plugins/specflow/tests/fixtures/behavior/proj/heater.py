def set_target(t):
    if t < 30 or t > 90:
        return "ERR_RANGE"
    return "OK"


def heater_on(target, actual):
    return actual <= target - 2
