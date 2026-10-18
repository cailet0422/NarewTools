// NarewDoom - DOOM (doomgeneric) on Islet Online's character select screen.
//
// Copyright (C) 2026 NarewTools contributors.  GPL-2.0-or-later (see LICENSE): this file is linked with
// doomgeneric / Chocolate Doom, so NarewDoom.dll as a whole is GPL.
//
// How it is wired (see tools/mod_doom.py and docs/10-code-patches.md):
//   * A code patch in NarewEngine2_Release64.dll calls NarewDoom_Frame(core, &focus) at the start of every
//     Nw::IGUICore::Render() (main thread, once per frame).  It loads this DLL itself (LoadLibraryW) the first time.
//   * The mod adds an image node with id DOOM_NODE_ID (31100) to Gui\character_select.xml.  While that node is
//     visible, DOOM runs in its own thread and every new DOOM frame is copied into the node's D3D9 texture
//     (CDirectX9Texture2D, D3DPOOL_MANAGED, so it can be locked).
//   * Clicking the DOOM picture gives it the keyboard: key messages of the game window are taken (subclassed
//     window procedure) and `focus` is set, which the same patch uses to make the engine's keyboard polling
//     (Nw::IKeyboard::Update -> GetAsyncKeyState) see no keys.  Clicking anywhere else gives the keyboard back.
//   * When the node is gone or hidden (game started, other screen), DOOM pauses (its clock stops, sound pauses).
//
// Everything that touches engine objects checks the NarewEngine2 build first and runs under SEH, so an
// unexpected engine never crashes the game: NarewDoom just turns itself off (see narewdoom.log).

#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <windows.h>
#include <shlobj.h>
#include <imm.h>
#include <d3d9.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <share.h>

#include "doomgeneric/doomgeneric.h"
#include "doomgeneric/doomkeys.h"
#include "narewdoom.h"

// ------------------------------------------------------------------ engine facts (NarewEngine2 x64)
// Known builds (PE TimeDateStamp); their code is byte for byte the same (2026-10-16 only changed timestamps and the
// PDB age), so every address below holds for all of them.  Another build: NarewDoom stays off (see the log).
#define DOOM_NODE_ID        31100
#define N2_MODULE           L"NarewEngine2_Release64.dll"
static const uint32_t N2_TIMESTAMPS[] = {
    0x6abf5433u,                                // 2026-10-03, sha256 40d2a2fc...
    0x6ad1b98bu,                                // 2026-10-16, sha256 3ce895c9...
};
#define N2_SIZE_OF_IMAGE    0x7b2000u
#define RVA_VT_GUIIMAGE     0x665b68u            // Nw::IGUIImage vftable
#define RVA_VT_D3D9TEX2D    0x6743c8u            // Nw::CDirectX9Texture2D vftable (primary)
#define VT_CORE_FIND        0x100                // IGUICore::Find(int id) -> IGUINode*
#define VT_CORE_MOUSE       0x158                // IGUICore -> Nw::IMouse*  (+0x38 HWND, +0x50/+0x54 GUI x/y)
#define VT_NODE_VISIBLE     0xb0                 // IGUINode::IsExactlyVisible() (node and all parents)
#define VT_NODE_POSON       0x2f8                // IGUINode::IsPosOn(float x, float y, int*, int*, float)
#define VT_IMAGE_TEXTURE    0x388                // IGUIImage::GetTexture() -> ITexture*
#define TEX_D3D             0x58                 // CDirectX9Texture2D: IDirect3DTexture9*
#define MOUSE_HWND          0x38
#define MOUSE_X             0x50
#define MOUSE_Y             0x54

// ------------------------------------------------------------------ picture layout (= the mod's placeholder PNG)
#define BORDER              4
#define TEX_W               (DOOMGENERIC_RESX + 2 * BORDER)
#define TEX_H               (DOOMGENERIC_RESY + 2 * BORDER)
#define COLOR_IDLE          0xff3a3a3au
#define COLOR_FOCUS         0xffd02020u

typedef void *(__fastcall *FindFn)(void *core, int id);
typedef void *(__fastcall *GetPtrFn)(void *self);
typedef int (__fastcall *IsVisibleFn)(void *node);
typedef int (__fastcall *IsPosOnFn)(void *node, float x, float y, int *lx, int *ly, float margin);

#define VCALL(obj, off, type) ((type)(*(void ***)(obj))[(off) / 8])

static HMODULE s_self;
static uintptr_t s_n2;                          // NarewEngine2 base, 0 = not checked yet
static volatile LONG s_disabled;                // 1 = engine not as expected or a fault happened: do nothing
static void *s_last_core;                       // the GUI core that showed the DOOM node last

// shared with the DOOM thread
static CRITICAL_SECTION s_lock;
static uint32_t *s_front;                       // last finished DOOM frame (RESX * RESY, 0x00RRGGBB)
static volatile LONG s_frame_no;
static volatile LONG s_active;                  // node visible: DOOM runs
static volatile LONG s_focus;                   // keyboard goes to DOOM
static volatile LONG s_failed;
static volatile ULONGLONG s_last_seen;
static HANDLE s_thread;
static char s_configdir[MAX_PATH * 2];
static char s_wad[MAX_PATH * 2];

// upload side (main thread)
static LONG s_uploaded_no = -1;
static int s_uploaded_focus = -1;
static void *s_uploaded_tex;
static int s_prev_lbutton;

// window / keys
static HWND s_hwnd;
static WNDPROC s_old_proc;
static HIMC s_old_imc;
static int s_imc_saved;
#define KEYQ 256
static uint16_t s_keyq[KEYQ];
static unsigned s_keyq_w, s_keyq_r;
static unsigned char s_down[256];               // doom keys currently held (to release them on focus loss)

// ------------------------------------------------------------------ log
static FILE *s_log;

void NarewDoom_Log(const char *fmt, ...)
{
    va_list ap;
    if (!s_log)
        return;
    va_start(ap, fmt);
    vfprintf(s_log, fmt, ap);
    va_end(ap);
    fputc('\n', s_log);
    fflush(s_log);
}

static void open_log(void)
{
    char path[MAX_PATH * 2];
    if (s_log || !s_configdir[0])
        return;
    _snprintf_s(path, sizeof(path), _TRUNCATE, "%snarewdoom.log", s_configdir);
    // (DOOM's own printf output is left alone: reopening stdout would close the game's standard handle)
    s_log = _fsopen(path, "w", _SH_DENYNO);
}

// ------------------------------------------------------------------ paths
static int wide_to_ansi(const wchar_t *w, char *out, int size)
{
    wchar_t shortp[MAX_PATH * 2];
    BOOL lossy = FALSE;
    int n = WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, w, -1, out, size, NULL, &lossy);
    if (n > 0 && !lossy)
        return 1;
    // a path the ANSI code page cannot spell: try its 8.3 name
    if (GetShortPathNameW(w, shortp, MAX_PATH * 2)) {
        n = WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, shortp, -1, out, size, NULL, &lossy);
        return n > 0 && !lossy;
    }
    return 0;
}

static void find_paths(void)
{
    wchar_t dir[MAX_PATH * 2], p[MAX_PATH * 2];
    static const wchar_t *wads[] = {
        L"doom2.wad", L"plutonia.wad", L"tnt.wad", L"doom.wad", L"doom1.wad", L"freedoom2.wad", L"freedoom1.wad",
    };
    int i;
    wchar_t *slash;

    // Documents\NarewStudio\NarewDoom\  (configs, savegames, logs); tests point NAREWDOOM_CONFIGDIR elsewhere
    if (GetEnvironmentVariableW(L"NAREWDOOM_CONFIGDIR", dir, MAX_PATH) && dir[0]) {
        if (dir[wcslen(dir) - 1] != L'\\')
            wcscat_s(dir, MAX_PATH * 2, L"\\");
        CreateDirectoryW(dir, NULL);
        if (!wide_to_ansi(dir, s_configdir, sizeof(s_configdir)))
            s_configdir[0] = 0;
    } else if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_PERSONAL | CSIDL_FLAG_CREATE, NULL, 0, dir))) {
        wcscat_s(dir, MAX_PATH * 2, L"\\NarewStudio");
        CreateDirectoryW(dir, NULL);
        wcscat_s(dir, MAX_PATH * 2, L"\\NarewDoom");
        CreateDirectoryW(dir, NULL);
        wcscat_s(dir, MAX_PATH * 2, L"\\");
        if (!wide_to_ansi(dir, s_configdir, sizeof(s_configdir)))
            s_configdir[0] = 0;
    }
    open_log();
    NarewDoom_Log("NarewDoom (doomgeneric %dx%d)", DOOMGENERIC_RESX, DOOMGENERIC_RESY);

    // <game>\NarewDoom\<iwad>  (next to this DLL)
    GetModuleFileNameW(s_self, dir, MAX_PATH * 2);
    slash = wcsrchr(dir, L'\\');
    if (slash)
        slash[1] = 0;
    wcscat_s(dir, MAX_PATH * 2, L"NarewDoom\\");
    for (i = 0; i < (int)(sizeof(wads) / sizeof(wads[0])); i++) {
        wcscpy_s(p, MAX_PATH * 2, dir);
        wcscat_s(p, MAX_PATH * 2, wads[i]);
        if (GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES && wide_to_ansi(p, s_wad, sizeof(s_wad))) {
            NarewDoom_Log("IWAD: %s", s_wad);
            return;
        }
    }
    s_wad[0] = 0;
    NarewDoom_Log("no IWAD in the game folder's NarewDoom\\ (doom1.wad, doom.wad, doom2.wad, ...)");
}

char *NarewDoom_ConfigDir(void)
{
    return s_configdir[0] ? s_configdir : ".\\";
}

// ------------------------------------------------------------------ DOOM thread
static LARGE_INTEGER s_qpf, s_qp0;
static volatile LONGLONG s_paused_ms;

static ULONGLONG now_ms(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (ULONGLONG)((t.QuadPart - s_qp0.QuadPart) * 1000 / s_qpf.QuadPart);
}

static int running_now(void)
{
    return s_active && GetTickCount64() - s_last_seen < 300;
}

static void wait_while_paused(void)
{
    ULONGLONG t0;
    if (running_now())
        return;
    t0 = now_ms();
    NarewSound_SetPaused(1);
    while (!running_now())
        Sleep(30);
    NarewSound_SetPaused(0);
    s_paused_ms += (LONGLONG)(now_ms() - t0);  // DOOM's clock does not see the pause
}

static DWORD WINAPI doom_thread(LPVOID arg)
{
    static char *argv[8];
    int argc = 0;
    (void)arg;
    argv[argc++] = "narewdoom";
    argv[argc++] = "-iwad";
    argv[argc++] = s_wad;
    argv[argc++] = "-nogui";
    argv[argc] = NULL;
    NarewDoom_Log("DOOM thread starts");
    doomgeneric_Create(argc, argv);
    NarewDoom_Log("DOOM is running");
    for (;;) {
        wait_while_paused();
        doomgeneric_Tick();
    }
}

void DG_Init(void) {}

void DG_DrawFrame(void)
{
    EnterCriticalSection(&s_lock);
    memcpy(s_front, DG_ScreenBuffer, DOOMGENERIC_RESX * DOOMGENERIC_RESY * 4);
    InterlockedIncrement(&s_frame_no);
    LeaveCriticalSection(&s_lock);
}

void DG_SleepMs(uint32_t ms)
{
    Sleep(ms);
}

uint32_t DG_GetTicksMs(void)
{
    return (uint32_t)(now_ms() - (ULONGLONG)s_paused_ms);
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    int got = 0;
    EnterCriticalSection(&s_lock);
    if (s_keyq_r != s_keyq_w) {
        uint16_t e = s_keyq[s_keyq_r % KEYQ];
        s_keyq_r++;
        *pressed = e >> 8;
        *key = (unsigned char)(e & 0xff);
        got = 1;
    }
    LeaveCriticalSection(&s_lock);
    return got;
}

void DG_SetWindowTitle(const char *title)
{
    (void)title;
}

void D_StartTitle(void);

void NarewDoom_Quit(void)
{
    NarewDoom_Log("Quit Game -> title screen");
    D_StartTitle();
}

void NarewDoom_Fatal(const char *message)
{
    NarewDoom_Log("DOOM error: %s", message);
    s_failed = 1;
    NarewSound_SetPaused(1);
    ExitThread(1);
}

__declspec(noreturn) void NarewDoom_Exit(int code)
{
    NarewDoom_Log("DOOM called exit(%d)", code);
    s_failed = 1;
    NarewSound_SetPaused(1);
    ExitThread((DWORD)code);
}

static void start_doom(void)
{
    if (s_thread || s_failed)
        return;
    QueryPerformanceFrequency(&s_qpf);
    QueryPerformanceCounter(&s_qp0);
    find_paths();
    if (!s_wad[0]) {
        s_failed = 1;
        return;
    }
    s_thread = CreateThread(NULL, 4 << 20, doom_thread, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
    if (!s_thread) {
        NarewDoom_Log("CreateThread failed (%lu)", GetLastError());
        s_failed = 1;
    }
}

// ------------------------------------------------------------------ keys (game window, main thread)
static unsigned char doom_key(WPARAM vk, LPARAM lp)
{
    if (vk >= 'A' && vk <= 'Z')
        return (unsigned char)(vk - 'A' + 'a');
    if (vk >= '0' && vk <= '9')
        return (unsigned char)vk;
    if (vk >= VK_F1 && vk <= VK_F10)
        return (unsigned char)(KEY_F1 + (vk - VK_F1));
    switch (vk) {
    case VK_LEFT: return KEY_LEFTARROW;
    case VK_RIGHT: return KEY_RIGHTARROW;
    case VK_UP: return KEY_UPARROW;
    case VK_DOWN: return KEY_DOWNARROW;
    case VK_CONTROL: return KEY_FIRE;
    case VK_SPACE: return KEY_USE;
    case VK_SHIFT: return KEY_RSHIFT;
    case VK_MENU: return KEY_RALT;
    case VK_RETURN: return KEY_ENTER;
    case VK_ESCAPE: return KEY_ESCAPE;
    case VK_TAB: return KEY_TAB;
    case VK_BACK: return KEY_BACKSPACE;
    case VK_PAUSE: return KEY_PAUSE;
    case VK_F11: return KEY_F11;
    case VK_F12: return KEY_F12;
    case VK_OEM_COMMA: return KEY_STRAFE_L;
    case VK_OEM_PERIOD: return KEY_STRAFE_R;
    case VK_OEM_MINUS: case VK_SUBTRACT: return KEY_MINUS;
    case VK_OEM_PLUS: case VK_ADD: return KEY_EQUALS;
    case VK_OEM_1: return ';';
    case VK_OEM_2: return '/';
    case VK_OEM_4: return '[';
    case VK_OEM_5: return '\\';
    case VK_OEM_6: return ']';
    case VK_OEM_7: return '\'';
    case VK_OEM_3: return '`';
    case VK_HOME: return KEY_HOME;
    case VK_END: return KEY_END;
    case VK_PRIOR: return KEY_PGUP;
    case VK_NEXT: return KEY_PGDN;
    case VK_INSERT: return KEY_INS;
    case VK_DELETE: return KEY_DEL;
    case VK_CAPITAL: return KEY_CAPSLOCK;
    }
    (void)lp;
    return 0;
}

static void push_key(int pressed, unsigned char key)
{
    if (!key)
        return;
    if (pressed && s_down[key])
        return;                                 // typematic repeat
    if (!pressed && !s_down[key])
        return;
    s_down[key] = (unsigned char)pressed;
    EnterCriticalSection(&s_lock);
    if (s_keyq_w - s_keyq_r < KEYQ) {
        s_keyq[s_keyq_w % KEYQ] = (uint16_t)((pressed << 8) | key);
        s_keyq_w++;
    }
    LeaveCriticalSection(&s_lock);
}

static void release_keys(void)
{
    int k;
    for (k = 1; k < 256; k++)
        if (s_down[k])
            push_key(0, (unsigned char)k);
}

static void set_focus(int on)
{
    on = on ? 1 : 0;
    if (on == s_focus)
        return;
    if (!on)
        release_keys();
    s_focus = on;
    if (s_hwnd) {                               // no IME composition while DOOM has the keyboard
        if (on) {
            s_old_imc = ImmAssociateContext(s_hwnd, NULL);
            s_imc_saved = 1;
        } else if (s_imc_saved) {
            ImmAssociateContext(s_hwnd, s_old_imc);
            s_imc_saved = 0;
        }
    }
    NarewDoom_Log("keyboard -> %s", on ? "DOOM" : "game");
}

static LRESULT CALLBACK doom_wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_KILLFOCUS || (msg == WM_ACTIVATE && LOWORD(wp) == WA_INACTIVE))
        release_keys();
    if (s_focus) {
        switch (msg) {
        case WM_SYSKEYDOWN:
            if (wp == VK_F4)                    // Alt+F4 still closes the game
                break;
            /* fall through */
        case WM_KEYDOWN:
            if (wp == VK_PROCESSKEY)
                wp = ImmGetVirtualKey(hwnd);
            push_key(1, doom_key(wp, lp));
            return 0;
        case WM_SYSKEYUP:
        case WM_KEYUP:
            if (wp == VK_PROCESSKEY)
                wp = ImmGetVirtualKey(hwnd);
            push_key(0, doom_key(wp, lp));
            return 0;
        case WM_CHAR:
        case WM_SYSCHAR:
        case WM_DEADCHAR:
        case WM_SYSDEADCHAR:
            return 0;
        }
    }
    return CallWindowProcA(s_old_proc, hwnd, msg, wp, lp);
}

static void hook_window(HWND hwnd)
{
    DWORD pid = 0;
    if (hwnd == s_hwnd || !hwnd || !IsWindow(hwnd))
        return;
    if (s_hwnd && IsWindow(s_hwnd))
        return;                                 // keep the window we already listen to
    s_hwnd = NULL;                              // that window is gone (recreated): listen to the new one
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid != GetCurrentProcessId())
        return;
    s_old_proc = (WNDPROC)SetWindowLongPtrA(hwnd, GWLP_WNDPROC, (LONG_PTR)doom_wndproc);
    if (s_old_proc) {
        s_hwnd = hwnd;
        NarewDoom_Log("keys: game window %p", (void *)hwnd);
    }
}

// ------------------------------------------------------------------ texture
static void compose_row(uint32_t *dst, int y, int w, const uint32_t *frame)
{
    uint32_t edge = s_focus ? COLOR_FOCUS : COLOR_IDLE;
    int x;
    if (y < BORDER || y >= BORDER + DOOMGENERIC_RESY) {
        for (x = 0; x < w; x++)
            dst[x] = edge;
        return;
    }
    for (x = 0; x < w; x++) {
        if (x < BORDER || x >= BORDER + DOOMGENERIC_RESX)
            dst[x] = edge;
        else
            dst[x] = frame ? (frame[(y - BORDER) * DOOMGENERIC_RESX + (x - BORDER)] | 0xff000000u) : 0xff000000u;
    }
}

static void upload(IDirect3DTexture9 *t, const uint32_t *frame)
{
    D3DSURFACE_DESC d;
    D3DLOCKED_RECT lr;
    DWORD levels, lv, locked = 0;
    int w, h, y, sw, sh;
    if (FAILED(IDirect3DTexture9_GetLevelDesc(t, 0, &d)))
        return;
    if (d.Format != D3DFMT_A8R8G8B8 && d.Format != D3DFMT_X8R8G8B8) {
        static int warned;
        if (!warned++)
            NarewDoom_Log("DOOM texture has format %d, not A8R8G8B8 - nothing drawn", (int)d.Format);
        return;
    }
    w = (int)d.Width < TEX_W ? (int)d.Width : TEX_W;
    h = (int)d.Height < TEX_H ? (int)d.Height : TEX_H;
    if (FAILED(IDirect3DTexture9_LockRect(t, 0, &lr, NULL, 0)))
        return;
    for (y = 0; y < h; y++)
        compose_row((uint32_t *)((uint8_t *)lr.pBits + (size_t)y * lr.Pitch), y, w, frame);
    // smaller mip levels (if the engine made any): 2x2 box filter from the level above
    levels = IDirect3DTexture9_GetLevelCount(t);
    sw = (int)d.Width;
    sh = (int)d.Height;
    for (lv = 1; lv < levels; lv++) {
        D3DLOCKED_RECT lo;
        D3DSURFACE_DESC dl;
        int x, lw, lh;
        if (FAILED(IDirect3DTexture9_GetLevelDesc(t, lv, &dl)) ||
            FAILED(IDirect3DTexture9_LockRect(t, lv, &lo, NULL, 0)))
            break;
        lw = (int)dl.Width;
        lh = (int)dl.Height;
        for (y = 0; y < lh; y++) {
            int y0 = 2 * y < sh ? 2 * y : sh - 1, y1 = 2 * y + 1 < sh ? 2 * y + 1 : sh - 1;
            uint32_t *o = (uint32_t *)((uint8_t *)lo.pBits + (size_t)y * lo.Pitch);
            const uint32_t *a = (const uint32_t *)((uint8_t *)lr.pBits + (size_t)y0 * lr.Pitch);
            const uint32_t *b = (const uint32_t *)((uint8_t *)lr.pBits + (size_t)y1 * lr.Pitch);
            for (x = 0; x < lw; x++) {
                int x0 = 2 * x < sw ? 2 * x : sw - 1, x1 = 2 * x + 1 < sw ? 2 * x + 1 : sw - 1;
                uint32_t p[4] = {a[x0], a[x1], b[x0], b[x1]};
                uint32_t r = 0, s;
                for (s = 0; s < 32; s += 8)
                    r |= ((((p[0] >> s) & 255) + ((p[1] >> s) & 255) + ((p[2] >> s) & 255) + ((p[3] >> s) & 255)) / 4) << s;
                o[x] = r;
            }
        }
        IDirect3DTexture9_UnlockRect(t, locked);
        locked = lv;
        lr = lo;
        sw = lw;
        sh = lh;
    }
    IDirect3DTexture9_UnlockRect(t, locked);
}

// ------------------------------------------------------------------ engine checks
static int engine_ok(void)
{
    HMODULE m;
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS64 *nt;
    int i, known = 0;
    if (s_n2)
        return 1;
    if (s_disabled)
        return 0;
    m = GetModuleHandleW(N2_MODULE);
    if (!m)
        return 0;
    dos = (IMAGE_DOS_HEADER *)m;
    nt = (IMAGE_NT_HEADERS64 *)((uint8_t *)m + dos->e_lfanew);
    InitializeCriticalSection(&s_lock);
    s_front = (uint32_t *)calloc(DOOMGENERIC_RESX * DOOMGENERIC_RESY, 4);
    for (i = 0; i < (int)(sizeof(N2_TIMESTAMPS) / sizeof(N2_TIMESTAMPS[0])); i++)
        known |= nt->FileHeader.TimeDateStamp == N2_TIMESTAMPS[i];
    if (!known || nt->OptionalHeader.SizeOfImage != N2_SIZE_OF_IMAGE || !s_front) {
        find_paths();
        NarewDoom_Log("NarewEngine2 is not a known build (stamp %08x, size %x) - NarewDoom is off",
                      nt->FileHeader.TimeDateStamp, nt->OptionalHeader.SizeOfImage);
        s_disabled = 1;
        return 0;
    }
    s_n2 = (uintptr_t)m;
    return 1;
}

static void lost_node(void *core, volatile int *focus)
{
    // the core that showed DOOM hides it, or another core renders and DOOM was not seen for 200 ms (the
    // character select GUI was destroyed): DOOM pauses and the game gets its keyboard back
    if (core == s_last_core || !s_last_core || GetTickCount64() - s_last_seen > 200) {
        s_active = 0;
        set_focus(0);
        *focus = 0;
    }
}

static int frame(void *core, volatile int *focus)
{
    void *node, *mouse, *tex;
    IDirect3DTexture9 *d3d;
    int lb;
    if (!core || !focus || !engine_ok())
        return 0;
    node = VCALL(core, VT_CORE_FIND, FindFn)(core, DOOM_NODE_ID);
    if (!node || *(uintptr_t *)node != s_n2 + RVA_VT_GUIIMAGE || !VCALL(node, VT_NODE_VISIBLE, IsVisibleFn)(node)) {
        lost_node(core, focus);
        return 0;
    }
    s_last_core = core;
    s_last_seen = GetTickCount64();
    s_active = 1;
    start_doom();

    // keyboard focus: a left click on the picture takes it, a click anywhere else gives it back
    mouse = VCALL(core, VT_CORE_MOUSE, GetPtrFn)(core);
    if (mouse)
        hook_window(*(HWND *)((uint8_t *)mouse + MOUSE_HWND));
    lb = (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
    if (lb && !s_prev_lbutton && mouse && s_hwnd && GetForegroundWindow() == s_hwnd) {
        float mx = (float)*(int *)((uint8_t *)mouse + MOUSE_X), my = (float)*(int *)((uint8_t *)mouse + MOUSE_Y);
        set_focus(!s_failed && VCALL(node, VT_NODE_POSON, IsPosOnFn)(node, mx, my, NULL, NULL, 0.0f));
    }
    s_prev_lbutton = lb;
    *focus = s_focus;

    // new DOOM frame (or focus colour) -> texture
    tex = VCALL(node, VT_IMAGE_TEXTURE, GetPtrFn)(node);
    if (!tex || *(uintptr_t *)tex != s_n2 + RVA_VT_D3D9TEX2D)
        return 1;
    d3d = *(IDirect3DTexture9 **)((uint8_t *)tex + TEX_D3D);
    if (!d3d)
        return 1;
    // (until DOOM's first frame the mod's placeholder picture stays: it says what to check if DOOM never starts)
    if (s_frame_no > 0 && (s_frame_no != s_uploaded_no || s_focus != s_uploaded_focus || tex != s_uploaded_tex)) {
        LONG no;
        EnterCriticalSection(&s_lock);
        no = s_frame_no;
        upload(d3d, s_front);
        LeaveCriticalSection(&s_lock);
        s_uploaded_no = no;
        s_uploaded_focus = s_focus;
        s_uploaded_tex = tex;
    }
    return 1;
}

// Called by the NarewEngine2 patch at the start of every IGUICore::Render() (main thread).
// `focus` is the patch's flag: non-zero = the engine's keyboard polling sees no keys.
__declspec(dllexport) int NarewDoom_Frame(void *core, volatile int *focus)
{
    int r = 0;
    if (s_disabled)
        return 0;
    __try {
        r = frame(core, focus);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        s_disabled = 1;
        s_focus = 0;
        if (focus)
            *focus = 0;
        NarewDoom_Log("fault %08lx in NarewDoom_Frame - NarewDoom is off", GetExceptionCode());
    }
    return r;
}

int NarewDoom_Silent(void)
{
    static int silent = -1;                     // NAREWDOOM_SILENT=1: sound runs but nothing reaches the speakers
    if (silent < 0) {
        wchar_t v[8];
        silent = GetEnvironmentVariableW(L"NAREWDOOM_SILENT", v, 8) && v[0] == L'1';
    }
    return silent;
}

#ifdef NAREWDOOM_TEST
// test build only (tests/test_narewdoom.py): force the keyboard focus, read counters
__declspec(dllexport) void NarewDoom_TestFocus(int on)
{
    set_focus(on);
}

__declspec(dllexport) void NarewDoom_TestInfo(char *buf, int size)
{
    _snprintf_s(buf, (size_t)size, _TRUNCATE, "frames=%ld active=%ld focus=%ld failed=%ld disabled=%ld sound=%s",
                s_frame_no, s_active, s_focus, s_failed, s_disabled, NarewSound_Stats());
}
#endif

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        s_self = inst;
        DisableThreadLibraryCalls(inst);
    }
    return TRUE;
}
