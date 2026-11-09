#ifndef TIMER_H
#define TIMER_H

/* Arms the one-shot link timer. timeout_ms is in milliseconds. */
void timer_arm(unsigned int timeout_ms);
unsigned int timer_deadline_ms(void);
void timer_set_now(unsigned int now_ms);

#endif
