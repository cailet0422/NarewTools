// NarewDoom: force-included into every doomgeneric source file (cl /FI).
// DOOM lives in a thread of the game process, so exit() must only end that thread, never the game.
#ifndef NAREW_SHIM_H
#define NAREW_SHIM_H

#define exit NarewDoom_Exit
__declspec(noreturn) void NarewDoom_Exit(int code);

#endif
