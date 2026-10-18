// Test harness for NarewDoom.dll (built and run by tests/test_narewdoom.py).
//
//   test_narewdoom.exe <NarewEngine2_Release64.dll (pristine 2026-10-03 build)> <NarewDoom.dll (test build)> <out dir>
//
// Plays the engine's part without the game: the real NarewEngine2 DLL is only mapped (no imports, no DllMain) so
// NarewDoom's build and vtable checks see the real addresses; the three IGUIImage vtable slots NarewDoom calls
// (IsExactlyVisible, IsPosOn, GetTexture) are pointed at fakes here, the GUI core and mouse are fake objects, and
// the texture is a real D3D9 MANAGED A8R8G8B8 texture with a full mip chain.  Keys go in as window messages.
// Prints "CHECK <name> OK|FAIL ..." lines and writes BMP snapshots of the texture.

#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include <stdint.h>

#define DOOM_NODE_ID 31100
#define RVA_VT_GUIIMAGE 0x665b68u
#define RVA_VT_D3D9TEX2D 0x6743c8u

typedef int (*FrameFn)(void *core, volatile int *focus);
typedef void (*FocusFn)(int on);
typedef void (*InfoFn)(char *buf, int size);

static int g_show = 1, g_fail;
static uint8_t g_mouse[0x60];
static uint8_t g_tex_obj[0x60];
static void *g_node[8];
static void *g_core_vt[0x200 / 8];
static void *g_core = g_core_vt;               // object = pointer to its vtable
static void *g_core_obj[1];

static void check(const char *name, int ok, const char *detail)
{
    printf("CHECK %s %s %s\n", name, ok ? "OK" : "FAIL", detail ? detail : "");
    if (!ok)
        g_fail = 1;
    fflush(stdout);
}

static void *fake_find(void *core, int id) { (void)core; return (id == DOOM_NODE_ID && g_show) ? g_node : NULL; }
static void *fake_mouse(void *core) { (void)core; return g_mouse; }
static int fake_visible(void *node) { (void)node; return g_show; }
static int fake_poson(void *node, float x, float y, int *lx, int *ly, float m)
{
    (void)node; (void)lx; (void)ly; (void)m;
    return x >= 40 && x < 40 + 648 && y >= 300 && y < 300 + 408;
}
static void *fake_gettex(void *node) { (void)node; return g_tex_obj; }

static LRESULT CALLBACK wndproc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

static void pump(void)
{
    MSG msg;
    while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
}

static void save_bmp(IDirect3DTexture9 *t, UINT level, const char *dir, const char *name)
{
    D3DSURFACE_DESC d;
    D3DLOCKED_RECT lr;
    char path[MAX_PATH];
    FILE *f;
    BITMAPFILEHEADER fh = {0};
    BITMAPINFOHEADER ih = {0};
    UINT y;
    IDirect3DTexture9_GetLevelDesc(t, level, &d);
    if (FAILED(IDirect3DTexture9_LockRect(t, level, &lr, NULL, D3DLOCK_READONLY)))
        return;
    sprintf_s(path, sizeof(path), "%s\\%s", dir, name);
    fopen_s(&f, path, "wb");
    if (f) {
        fh.bfType = 0x4d42;
        fh.bfOffBits = sizeof(fh) + sizeof(ih);
        fh.bfSize = fh.bfOffBits + d.Width * d.Height * 4;
        ih.biSize = sizeof(ih);
        ih.biWidth = (LONG)d.Width;
        ih.biHeight = -(LONG)d.Height;
        ih.biPlanes = 1;
        ih.biBitCount = 32;
        fwrite(&fh, sizeof(fh), 1, f);
        fwrite(&ih, sizeof(ih), 1, f);
        for (y = 0; y < d.Height; y++)
            fwrite((uint8_t *)lr.pBits + y * lr.Pitch, 4, d.Width, f);
        fclose(f);
    }
    IDirect3DTexture9_UnlockRect(t, level);
}

static uint32_t texel(IDirect3DTexture9 *t, int x, int y)
{
    D3DLOCKED_RECT lr;
    uint32_t v = 0;
    if (SUCCEEDED(IDirect3DTexture9_LockRect(t, 0, &lr, NULL, D3DLOCK_READONLY))) {
        v = ((uint32_t *)((uint8_t *)lr.pBits + y * lr.Pitch))[x];
        IDirect3DTexture9_UnlockRect(t, 0);
    }
    return v;
}

static int distinct_colors(IDirect3DTexture9 *t)
{
    // how many different colours in the DOOM area (a black/flat texture means nothing was drawn)
    D3DLOCKED_RECT lr;
    static uint32_t seen[4096];
    int n = 0, x, y, i;
    if (FAILED(IDirect3DTexture9_LockRect(t, 0, &lr, NULL, D3DLOCK_READONLY)))
        return 0;
    for (y = 4; y < 404 && n < 4096; y += 3)
        for (x = 4; x < 644 && n < 4096; x += 3) {
            uint32_t v = ((uint32_t *)((uint8_t *)lr.pBits + y * lr.Pitch))[x];
            for (i = 0; i < n && seen[i] != v; i++)
                ;
            if (i == n)
                seen[n++] = v;
        }
    IDirect3DTexture9_UnlockRect(t, 0);
    return n;
}

static void key(HWND h, UINT vk, int down)
{
    PostMessageA(h, down ? WM_KEYDOWN : WM_KEYUP, vk, down ? 1 : (LPARAM)0xC0000001);
}

static ULONGLONG g_t0;
static ULONGLONG elapsed(void) { return GetTickCount64() - g_t0; }

static void run(FrameFn frame, volatile int *focus, ULONGLONG until)
{
    while (elapsed() < until) {
        pump();
        frame(&g_core_obj, focus);
        Sleep(15);
    }
}

// ---------------------------------------------------------------- --engine: run the PATCHED NarewEngine2 code
// The mapped (patched) DLL's IGUICore::Render() is called with a fake core: the patch must load NarewDoom.dll from
// this exe's folder by name, call NarewDoom_Frame(core, &focus), and then carry on with the original Render body
// (two virtual calls on the core's members).  The keyboard gate is called directly.
#define RVA_RENDER 0x32930u
#define RVA_STATE 0x755f00u
#define RVA_IAT_LOADLIBRARYW 0x55c300u
#define RVA_IAT_GETPROCADDRESS 0x55c400u
#define RVA_IAT_GETASYNCKEYSTATE 0x55cbf0u
#define RVA_KEY_GATE 0x25280u

static int g_member_a, g_member_b;
static uint8_t g_ecore[0x900];                 // the fake core in --engine mode
static void member_a(void *self, void *core, int z) { (void)self; (void)z; g_member_a += core == (void *)g_ecore; }
static void member_b(void *self) { (void)self; g_member_b++; }
static SHORT WINAPI fake_gaks(int vk) { return vk == 'W' ? (SHORT)0x8001 : 0; }

static int engine_mode(HMODULE n2, IDirect3DTexture9 *tex, const char *out)
{
    static void *vt_a[0x100 / 8], *vt_b[0x48 / 8], *obj_a[1], *obj_b[1];
    uint8_t *core = g_ecore;
    void (*render)(void *) = (void (*)(void *))((uint8_t *)n2 + RVA_RENDER);
    SHORT (*gate)(int) = (SHORT (*)(int))((uint8_t *)n2 + RVA_KEY_GATE);
    uint8_t *state = (uint8_t *)n2 + RVA_STATE;
    void **iat;
    DWORD old;
    HMODULE nd;
    InfoFn info;
    FocusFn set_focus;
    char buf[512], detail[700];
    long frames = 0;
    int i;
    (void)out;
    // resolve the three imports the patch uses (the DLL is only mapped)
    iat = (void **)((uint8_t *)n2 + RVA_IAT_LOADLIBRARYW);
    VirtualProtect(iat, 8, PAGE_READWRITE, &old);
    *iat = (void *)LoadLibraryW;
    iat = (void **)((uint8_t *)n2 + RVA_IAT_GETPROCADDRESS);
    VirtualProtect(iat, 8, PAGE_READWRITE, &old);
    *iat = (void *)GetProcAddress;
    iat = (void **)((uint8_t *)n2 + RVA_IAT_GETASYNCKEYSTATE);
    VirtualProtect(iat, 8, PAGE_READWRITE, &old);
    *iat = (void *)fake_gaks;
    // the core: vtable (Find, mouse) + the members the original Render() body uses
    vt_a[0xe8 / 8] = (void *)member_a;
    vt_b[0x40 / 8] = (void *)member_b;
    obj_a[0] = vt_a;
    obj_b[0] = vt_b;
    *(void **)core = g_core_vt;
    *(void **)(core + 0x130) = obj_a;
    *(void **)(core + 0x660) = obj_b;
    *(void **)(core + 0x8f8) = NULL;

    check("engine_before", GetModuleHandleW(L"NarewDoom.dll") == NULL && *(uint64_t *)(state + 8) == 0, NULL);
    check("gate_passes", gate('W') == (SHORT)0x8001 && gate('A') == 0, "focus 0: GetAsyncKeyState as is");
    g_t0 = GetTickCount64();
    while (elapsed() < 3500) {
        pump();
        render(core);
        Sleep(15);
    }
    nd = GetModuleHandleW(L"NarewDoom.dll");
    info = nd ? (InfoFn)GetProcAddress(nd, "NarewDoom_TestInfo") : NULL;
    set_focus = nd ? (FocusFn)GetProcAddress(nd, "NarewDoom_TestFocus") : NULL;
    check("engine_loaded_dll", nd != NULL && info && set_focus, "LoadLibraryW(L\"NarewDoom.dll\") from the patch");
    if (!info || !set_focus)
        return 1;
    check("engine_state", *(void **)(state + 8) == (void *)GetProcAddress(nd, "NarewDoom_Frame") &&
          *(int *)(state + 0x10) == 1, "state block: proc, tried");
    sprintf_s(detail, sizeof(detail), "member_a %d, member_b %d calls", g_member_a, g_member_b);
    check("engine_original_body", g_member_a > 100 && g_member_b == g_member_a, detail);
    info(buf, sizeof(buf));
    sscanf_s(buf, "frames=%ld", &frames);
    sprintf_s(detail, sizeof(detail), "%s texel(20,20)=%08x", buf, texel(tex, 20, 20));
    check("engine_doom_running", frames > 20 && texel(tex, 20, 20) != 0xff00ff00u, detail);
    set_focus(1);
    for (i = 0; i < 5; i++) {
        pump();
        render(core);
        Sleep(15);
    }
    check("engine_focus_flag", *(int *)state == 1, NULL);
    check("gate_blocks", gate('W') == 0, "focus 1: no keys for the game");
    set_focus(0);
    for (i = 0; i < 5; i++) {
        pump();
        render(core);
        Sleep(15);
    }
    check("engine_focus_back", *(int *)state == 0 && gate('W') == (SHORT)0x8001, NULL);
    // node gone (game started): the flag must clear even if only another core renders
    set_focus(1);
    render(core);
    g_show = 0;
    for (i = 0; i < 3; i++) {
        render(core);
        Sleep(15);
    }
    check("engine_focus_cleared_without_node", *(int *)state == 0, NULL);
    // node in a core that stops rendering, another core renders on: the flag clears after ~200 ms
    {
        static void *vt2[0x200 / 8];
        static uint8_t core2[0x900];
        memcpy(core2, core, sizeof(core2));
        vt2[0x100 / 8] = (void *)fake_find;     // fake_find: no node while g_show == 0
        vt2[0x158 / 8] = (void *)fake_mouse;
        *(void **)core2 = vt2;
        g_show = 1;
        for (i = 0; i < 3; i++) {
            render(core);
            Sleep(15);
        }
        set_focus(1);
        render(core);
        check("engine_focus_on_again", *(int *)state == 1, NULL);
        g_show = 0;                             // core2 never has the node; core is not rendered any more
        for (i = 0; i < 25; i++) {
            render(core2);
            Sleep(15);
        }
        check("engine_focus_cleared_other_core", *(int *)state == 0, NULL);
    }
    save_bmp(tex, 0, out, "6_engine.bmp");
    printf("DONE %s\n", g_fail ? "FAIL" : "OK");
    fflush(stdout);
    ExitProcess(g_fail ? 1 : 0);
}

int main(int argc, char **argv)
{
    HMODULE n2, nd;
    WNDCLASSA wc = {0};
    HWND hwnd;
    IDirect3D9 *d3d;
    IDirect3DDevice9 *dev = NULL;
    IDirect3DTexture9 *tex = NULL;
    D3DPRESENT_PARAMETERS pp = {0};
    void **vt;
    DWORD old;
    FrameFn frame;
    FocusFn set_focus;
    InfoFn info;
    volatile int focus = 0;
    char buf[512], detail[600];
    wchar_t wpath[MAX_PATH];
    long f1, f2, f3;
    int r;
    int engine = argc >= 2 && strcmp(argv[1], "--engine") == 0;

    if (argc < 4) {
        printf("usage: test_narewdoom <NarewEngine2 dll> <NarewDoom dll> <out dir>\n"
               "       test_narewdoom --engine <patched NarewEngine2 dll> <out dir>\n");
        return 2;
    }
    if (engine)                                 // --engine <patched NarewEngine2 dll> <out dir>
        argv[1] = argv[2];
    wc.lpfnWndProc = wndproc;
    wc.lpszClassName = "NarewDoomTest";
    RegisterClassA(&wc);
    hwnd = CreateWindowA("NarewDoomTest", "NarewDoom test", WS_OVERLAPPEDWINDOW, 0, 0, 800, 600, 0, 0, 0, 0);
    d3d = Direct3DCreate9(D3D_SDK_VERSION);
    pp.Windowed = TRUE;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.BackBufferFormat = D3DFMT_UNKNOWN;
    if (d3d && FAILED(IDirect3D9_CreateDevice(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, hwnd,
                                              D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev)))
        dev = NULL;
    check("d3d9_device", dev != NULL, NULL);
    if (!dev)
        return 1;
    IDirect3DDevice9_CreateTexture(dev, 648, 408, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &tex, NULL);
    check("texture", tex != NULL, NULL);
    if (!tex)
        return 1;
    {   // placeholder colour, so "nothing uploaded yet" is visible
        D3DLOCKED_RECT lr;
        int y, x;
        IDirect3DTexture9_LockRect(tex, 0, &lr, NULL, 0);
        for (y = 0; y < 408; y++)
            for (x = 0; x < 648; x++)
                ((uint32_t *)((uint8_t *)lr.pBits + y * lr.Pitch))[x] = 0xff00ff00u;
        IDirect3DTexture9_UnlockRect(tex, 0);
    }

    MultiByteToWideChar(CP_ACP, 0, argv[1], -1, wpath, MAX_PATH);
    n2 = LoadLibraryExW(wpath, NULL, DONT_RESOLVE_DLL_REFERENCES);
    check("n2_mapped", n2 != NULL && GetModuleHandleW(L"NarewEngine2_Release64.dll") == n2, argv[1]);
    if (!n2)
        return 1;
    vt = (void **)((uint8_t *)n2 + RVA_VT_GUIIMAGE);
    VirtualProtect(vt, 0x400, PAGE_READWRITE, &old);
    vt[0xb0 / 8] = (void *)fake_visible;
    vt[0x2f8 / 8] = (void *)fake_poson;
    vt[0x388 / 8] = (void *)fake_gettex;
    g_node[0] = vt;
    *(void **)g_tex_obj = (uint8_t *)n2 + RVA_VT_D3D9TEX2D;
    *(IDirect3DTexture9 **)(g_tex_obj + 0x58) = tex;
    g_core_vt[0x100 / 8] = (void *)fake_find;
    g_core_vt[0x158 / 8] = (void *)fake_mouse;
    g_core_obj[0] = g_core_vt;
    *(HWND *)(g_mouse + 0x38) = hwnd;
    if (engine)
        return engine_mode(n2, tex, argv[3]);

    MultiByteToWideChar(CP_ACP, 0, argv[2], -1, wpath, MAX_PATH);
    nd = LoadLibraryW(wpath);
    check("narewdoom_loaded", nd != NULL, argv[2]);
    if (!nd)
        return 1;
    frame = (FrameFn)GetProcAddress(nd, "NarewDoom_Frame");
    set_focus = (FocusFn)GetProcAddress(nd, "NarewDoom_TestFocus");
    info = (InfoFn)GetProcAddress(nd, "NarewDoom_TestInfo");
    check("exports", frame && set_focus && info, NULL);
    if (!frame || !set_focus || !info)
        return 1;

    // no DOOM node in this core -> 0, nothing started
    g_show = 0;
    r = frame(&g_core_obj, &focus);
    check("no_node", r == 0 && focus == 0, NULL);
    g_show = 1;
    g_t0 = GetTickCount64();

    run(frame, &focus, 3500);                   // DOOM starts, title screen / demo
    info(buf, sizeof(buf));
    sscanf_s(buf, "frames=%ld", &f1);
    sprintf_s(detail, sizeof(detail), "%s texel(20,20)=%08x", buf, texel(tex, 20, 20));
    check("doom_running", f1 > 20 && texel(tex, 20, 20) != 0xff00ff00u, detail);
    check("border_idle", texel(tex, 1, 1) == 0xff3a3a3au && texel(tex, 646, 406) == 0xff3a3a3au, NULL);
    sprintf_s(detail, sizeof(detail), "%d colours", distinct_colors(tex));
    check("picture", distinct_colors(tex) > 16, detail);
    save_bmp(tex, 0, argv[3], "1_title.bmp");

    // node hidden -> DOOM pauses (no new frames), shown again -> runs again
    g_show = 0;
    run(frame, &focus, elapsed() + 600);
    info(buf, sizeof(buf));
    sscanf_s(buf, "frames=%ld", &f1);
    run(frame, &focus, elapsed() + 1000);
    info(buf, sizeof(buf));
    sscanf_s(buf, "frames=%ld", &f2);
    g_show = 1;
    run(frame, &focus, elapsed() + 1000);
    info(buf, sizeof(buf));
    sscanf_s(buf, "frames=%ld", &f3);
    sprintf_s(detail, sizeof(detail), "frames %ld -> %ld while hidden, %ld after showing", f1, f2, f3);
    check("pause_when_hidden", f2 - f1 <= 1 && f3 - f2 > 15, detail);

    // keyboard: focus on (as a click on the picture would), start a new game with the keys
    set_focus(1);
    run(frame, &focus, elapsed() + 100);
    check("focus_flag", focus == 1 && texel(tex, 1, 1) == 0xffd02020u, NULL);
    key(hwnd, VK_ESCAPE, 1); run(frame, &focus, elapsed() + 120); key(hwnd, VK_ESCAPE, 0);
    run(frame, &focus, elapsed() + 600);
    save_bmp(tex, 0, argv[3], "2_menu.bmp");
    key(hwnd, VK_RETURN, 1); run(frame, &focus, elapsed() + 120); key(hwnd, VK_RETURN, 0);   // New Game
    run(frame, &focus, elapsed() + 500);
    key(hwnd, VK_RETURN, 1); run(frame, &focus, elapsed() + 120); key(hwnd, VK_RETURN, 0);   // episode
    run(frame, &focus, elapsed() + 500);
    key(hwnd, VK_RETURN, 1); run(frame, &focus, elapsed() + 120); key(hwnd, VK_RETURN, 0);   // skill
    run(frame, &focus, elapsed() + 2500);
    save_bmp(tex, 0, argv[3], "3_e1m1.bmp");
    key(hwnd, VK_UP, 1); run(frame, &focus, elapsed() + 1200); key(hwnd, VK_UP, 0);
    key(hwnd, VK_CONTROL, 1); run(frame, &focus, elapsed() + 150); key(hwnd, VK_CONTROL, 0);
    run(frame, &focus, elapsed() + 800);
    save_bmp(tex, 0, argv[3], "4_moved_fired.bmp");
    save_bmp(tex, 1, argv[3], "5_mip1.bmp");
    // focus off: flag back to 0, border idle again
    set_focus(0);
    run(frame, &focus, elapsed() + 200);
    check("focus_released", focus == 0 && texel(tex, 1, 1) == 0xff3a3a3au, NULL);
    info(buf, sizeof(buf));
    printf("INFO %s\n", buf);
    check("not_failed", strstr(buf, "failed=0 disabled=0") != NULL, buf);
    printf("DONE %s\n", g_fail ? "FAIL" : "OK");
    fflush(stdout);
    ExitProcess(g_fail ? 1 : 0);
}
