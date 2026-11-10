void temp_update(void);
int temp_celsius_x10(void);
void heater_set(int on);

static int target_x10 = 600;

void ctrl_step(void)
{
    int t;

    temp_update();
    t = temp_celsius_x10();
    heater_set(t <= target_x10 - 20);
}
