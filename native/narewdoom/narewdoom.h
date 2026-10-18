// NarewDoom shared declarations (GPL-2.0-or-later, see LICENSE)
#ifndef NAREWDOOM_H
#define NAREWDOOM_H

void NarewDoom_Log(const char *fmt, ...);
int NarewDoom_Silent(void);                 // NAREWDOOM_SILENT=1 (tests): mix and sequence, but output nothing
void NarewSound_SetPaused(int paused);      // DOOM not on screen: sound effects and music pause
const char *NarewSound_Stats(void);         // counters for the tests

#endif
