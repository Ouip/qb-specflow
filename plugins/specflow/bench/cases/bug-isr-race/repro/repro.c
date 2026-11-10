/* Models the interrupt as a second thread. ThreadSanitizer reports the unsynchronized
 * read-modify-write on `pending` (exit 66), the same window an interrupt hits on one core. */
#include <pthread.h>
#include <stdio.h>

void uart_rx_isr(void);
int app_take_event(void);

static void *isr(void *arg)
{
    (void)arg;
    for (int i = 0; i < 100000; i++) {
        uart_rx_isr();
    }
    return NULL;
}

int main(void)
{
    pthread_t t;
    long taken = 0;
    pthread_create(&t, NULL, isr, NULL);
    for (int i = 0; i < 200000; i++) {
        taken += app_take_event();
    }
    pthread_join(t, NULL);
    while (app_take_event()) {
        taken++;
    }
    printf("events taken %ld of 100000\n", taken);
    return 0;
}
