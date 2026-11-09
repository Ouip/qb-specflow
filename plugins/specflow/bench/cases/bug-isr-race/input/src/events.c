static unsigned int pending;

/* UART receive interrupt: one call per received byte */
void uart_rx_isr(void)
{
    pending++;
}

/* main loop: returns 1 and consumes one event if there is one */
int app_take_event(void)
{
    if (pending == 0) {
        return 0;
    }
    pending--;
    return 1;
}
