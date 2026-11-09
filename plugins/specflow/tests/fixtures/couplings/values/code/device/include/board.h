#ifndef BOARD_H
#define BOARD_H

#if defined(BOARD_DEV)
#define FRAME_W 320
#elif defined(BOARD_PROD)
#define FRAME_W (2 * 240)
#else
#error "frame width for this board not decided"
#endif

#endif
