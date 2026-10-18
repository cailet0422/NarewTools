"""Build the "free camera" mod (engine code patches + GUI buttons) for the 64-bit client (builds of 2026-10-03, -16).

    python tools/mod_freecam.py <game folder> <mod project folder> [--speed 20] [--fast 4] [--dry-run]

The game folder is only read: the untouched engine DLLs (``<dll>.orig`` when the mod manager already patched
one), Gui\\game_menu_top.xml / Gui\\game_menu.xml and two gui_skin pictures.

What the mod does:
  * a "프리캠" button left of the shop button (top menu, both menu layouts) enters free camera mode
  * in free camera mode W/S move along the view, A/D sideways, Space up, Shift down, Ctrl = 4x faster; the mouse
    (right drag, or mouse-look in lock mode) and the arrow/Q/E keys turn the camera as usual
  * the character is not controlled (keyboard and mouse actions are locked as while chatting; it stops)
  * every GUI window is hidden except a "프리캠 종료" button (same place); it or ESC goes back. Name boards, speech
    bubbles and number effects (damage etc.) above characters are hidden too
  * region (chunk) loading follows the camera by itself: IBrickGroupManager::UpdateRegion centres the region spiral on
    the camera's look-at point, which in free camera mode is one brick in front of the eye

How (REA, see docs/10-code-patches.md):
  NarewEngine2_Release64.dll (GUI)
    - state block in the unused tail of .data (its virtual size is raised to the page end)
    - IGUICore::UpdateEvent: the event queue dispatch looks at the event id first: 31001 = enter, 31002 = leave
      (the click never reaches the game's own menu handler); in free camera mode only the exit button node gets
      mouse events, and the exit button's visibility follows the mode
    - IGUICore::Render: in free camera mode only the exit button node is drawn
  IsletEngine_release(x64).dll (game)
    - CGameCamera::Update: in free camera mode the mode switch calls our update instead of UpdateFPS/UpdateRPG
    - CGameControl: the keyboard/mouse "locked" checks (UpdateNormalMode, UpdateFalling, Update) also count free
      camera mode, auto-move is ignored, and ESC (key released) leaves free camera mode instead of opening the menu
    - AddDrawName / CSpeechBubbleMgr::Render / CNumberEffectMgr::Render take their "nothing to draw" exit
    - the IsletEngine code finds the NarewEngine2 state through its import of Nw::Interpolate and a signature at
      the start of the NarewEngine2 code, so it does nothing when that DLL is not patched
  New code lives in compiler-made exports nothing calls: IGUICore's copy constructor (NarewEngine2) and COption's
  copy constructor (IsletEngine; the view distance mod uses CSky::operator=, so both mods fit together).
Both targets are pinned to the sha256 of the DLLs they are built from (N2_SHAS / IE_BUILDS list the known builds)
and the mod manager applies the patch file to both DLLs or to neither.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from narew import codepatch  # noqa: E402
from narew.modfmt import ModFolder  # noqa: E402

BASE = 0x180000000
ID_ENTER, ID_EXIT = 31001, 31002            # node id = mouse_click event id of the two buttons
SIG = b'NWFRCAM1'

# ---------------------------------------------------------------- NarewEngine2_Release64.dll
N2_FILE = 'NarewEngine2_Release64.dll'
# known builds; the Steam update of 2026-10-16 rebuilt the DLL with byte-identical code (only the PE/debug timestamps
# and the PDB age differ), so the addresses below hold for both
N2_SHAS = {
    '40d2a2fc50b3cc3e235ca2afcdc18cfe1f679c921528fecbc15a2191b6adb23c': '2026-10-03',
    '3ce895c96dbadc050f4e3d34d8bc85861fcea15a67da32efc70055f5c17ee288': '2026-10-16',
}
N2_CAVE = 0x180024bf0                       # ??0IGUICore@Nw@@QEAA@AEBV01@@Z (1457 bytes, never called)
N2_CAVE_SYMBOL = '??0IGUICore@Nw@@QEAA@AEBV01@@Z'
N2_CAVE_SIZE = 1457
N2_STATE = 0x180755c00                      # .data ends at 0x1807558e8 (virtual), page ends at 0x180756000
N2_DATA_VSIZE_FIELD, N2_DATA_VSIZE, N2_DATA_VSIZE_NEW = 0x298, 0x188e8, 0x19000
N2_EVT_GATE = 0x1800326fc                   # UpdateEvent: test rbx,rbx ; je 0x180032772   (root loop start)
N2_EVT_LOOP, N2_EVT_NOROOT, N2_EVT_QUEUE = 0x180032701, 0x180032772, 0x18003279f
N2_DISPATCH = 0x1800327e4                   # mov r8,[rax] ; mov rdx,[rbx] ; mov rcx,rax ; call [r8]  (12 bytes)
N2_RENDER_LOOP = 0x180032c39                # Render(pass, draw): mov rax,[rdi+0x120]  (7 bytes)
N2_RENDER_LOOP_NEXT, N2_RENDER_DONE = 0x180032c40, 0x180032c6b
VT_CORE_FIND = 0x100                        # IGUICore::Find(int id) -> node (searches every root)
VT_NODE_RENDER, VT_NODE_VISIBLE, VT_NODE_EVENTS = 0x90, 0xa8, 0x290  # Render(pass, draw) / SetVisible / UpdateEvent
VT_EVENT_ID = 0x70                          # IGUIEvent::GetEvent

# ---------------------------------------------------------------- IsletEngine_release(x64).dll
IE_FILE = 'IsletEngine_release(x64).dll'
# addresses (VA) per known build: sha256 -> {name: va}; build_ie() points the module constants at the build it gets.
#   IE_CAVE           ??0COption@Islet@@QEAA@AEBV01@@Z (2204 bytes, never called)
#   CAM_DISPATCH      CGameCamera::Update: test ecx,ecx ... call UpdateRPG/UpdateFPS (26 bytes up to CAM_DISPATCH_END)
#   UPDATE_FPS, UPDATE_RPG, ROT_R, ROT_LOCK   CGameCamera::UpdateFPS / UpdateRPG / UpdateMouseRotR / UpdateMouseRotLock
#                                             (the last two: this, elapsed, 0)
#   NM_KB             UpdateNormalMode: cmp byte [rcx+0x262],0 (keyboard locked?)
#   NM_AUTO           UpdateNormalMode: cmp byte [rbx+0x261],0 (auto move?)
#   NM_MOUSE          UpdateNormalMode: cmp byte [rbx+0x263],0 (mouse locked?)
#   FALL_KB           UpdateFalling: cmp byte [rdi+0x262],0 (stop the walk when locked)
#   UPD_ESC           CGameControl::Update: cmp byte [rdi+0x91],3 (ESC released -> menu callback)
#   UPD_KB            CGameControl::Update: cmp byte [rdi+0x262],0 (R key callback)
#   NAMES             ICreatureRenderable::AddDrawName: cmp qword [rcx+0x1c0],0 ; .. jne out
#   BUBBLES           CSpeechBubbleMgr::Render(): cmp dword [rcx+0x530],0 ; .. jle out
#   NUMBERS           CNumberEffectMgr::Render(): mov rcx,[rcx+0x200] ; test rcx,rcx ; je out
# The Steam update of 2026-10-16 changed IsletEngine's code elsewhere: every function this patch touches or calls is
# instruction for instruction the same in both builds (branch and RIP targets included, capstone), only moved.
IE_BUILDS = {
    '8e4c0cd9ca4f9308db163aefe5f8e14fc3020ab1a1b03a260d3b0955d123e3cf': dict(
        DATE='2026-10-03', IE_CAVE=0x1800fb310, CAM_DISPATCH=0x1800f8d2e, CAM_DISPATCH_END=0x1800f8d48,
        UPDATE_FPS=0x1800f9820, UPDATE_RPG=0x1800f9aa0, ROT_R=0x1800fa2c0, ROT_LOCK=0x1800fa770,
        NM_KB=0x1801002da, NM_AUTO=0x180100355, NM_MOUSE=0x180100368, FALL_KB=0x1800fed0f,
        UPD_ESC=0x1800fd24d, UPD_KB=0x1800fd268, NAMES=0x1800dcba7, BUBBLES=0x1800f70db, NUMBERS=0x1800f56b9),
    'abe3d06c6c0c9de8914e6dcb59b8580c48cfa84ea1c1e13d2aa42cfdd9ff0670': dict(
        DATE='2026-10-16', IE_CAVE=0x1800fb730, CAM_DISPATCH=0x1800f915e, CAM_DISPATCH_END=0x1800f9178,
        UPDATE_FPS=0x1800f9c70, UPDATE_RPG=0x1800f9ef0, ROT_R=0x1800fa710, ROT_LOCK=0x1800fabc0,
        NM_KB=0x1801006ea, NM_AUTO=0x180100765, NM_MOUSE=0x180100778, FALL_KB=0x1800ff12f,
        UPD_ESC=0x1800fd66d, UPD_KB=0x1800fd688, NAMES=0x1800dc947, BUBBLES=0x1800f739b, NUMBERS=0x1800f5689),
}
IE_CAVE_SYMBOL = '??0COption@Islet@@QEAA@AEBV01@@Z'
IE_CAVE_SIZE = 2204
IE_SYMBOLS = {'UPDATE_FPS': '?UpdateFPS@CGameCamera@Islet@@IEAAXK@Z',      # the functions the new code calls
              'UPDATE_RPG': '?UpdateRPG@CGameCamera@Islet@@IEAAXK@Z',
              'ROT_R': '?UpdateMouseRotR@CGameCamera@Islet@@IEAAHKH@Z',
              'ROT_LOCK': '?UpdateMouseRotLock@CGameCamera@Islet@@IEAAHKH@Z'}
IE_ANCHOR_IAT = 0x180206530                 # [IAT] NarewEngine2!?Interpolate@Nw@@YAMMMM@Z (same slot in both builds)
ANCHOR_SYMBOL = '?Interpolate@Nw@@YAMMMM@Z'


def use_ie_build(dll: bytes) -> str:
    """Point the IsletEngine constants at the build of ``dll``; returns its date (SystemExit for an unknown build)."""
    sha = codepatch.sha256(dll)
    if sha not in IE_BUILDS:
        raise SystemExit(f'{IE_FILE}: 아는 빌드(' + ', '.join(v['DATE'] for v in IE_BUILDS.values())
                         + ')의 원본이 아닙니다 (sha256 불일치).')
    globals().update(IE_BUILDS[sha])
    return IE_BUILDS[sha]['DATE']


globals().update(list(IE_BUILDS.values())[-1])     # the newest build until build_ie() picks the one it is given

# CGameCamera members
CAM_MODE, CAM_NCAM, CAM_CTRL, CAM_YAW, CAM_PITCH, CAM_LOCKMODE = 0x60, 0x40, 0x50, 0xf0, 0xf4, 0x124
# Nw::ICamera: eye +0x14, at +0x20 ; vtable SetEye 0x78, SetYawPitchRoll 0xf8 (yaw, pitch, roll, distance), GetRoll 0x110
NCAM_EYE, NCAM_AT = 0x14, 0x20
VT_SETEYE, VT_YPR, VT_GETROLL = 0x78, 0xf8, 0x110
# CGameControl key states (byte per GAME_KEY_MAP, 1 = pressed, 2 = held, 3 = released) at +0x5c
KEYS = 0x5c
K_FWD, K_BACK, K_LEFT, K_RIGHT, K_JUMP, K_CTRL, K_SHIFT = 0, 1, 2, 3, 4, 6, 9

# state block (NarewEngine2 .data tail)
ST_ACTIVE, ST_SHOWN, ST_INIT, ST_POS = 0x0, 0x4, 0x8, 0x10


def f32(x: float) -> bytes:
    return struct.pack('<f', x)


def u32(x: int) -> bytes:
    return struct.pack('<I', x & 0xffffffff)


def i32(x: int) -> bytes:
    return struct.pack('<i', x)


class Asm:
    """Byte emitter with labels (short or near jumps); the instructions are spelled out by hand below."""

    SHORT = {'jmp': 'eb', 'je': '74', 'jz': '74', 'jne': '75', 'jnz': '75', 'ja': '77', 'jbe': '76'}
    NEAR = {'jmp': 'e9', 'je': '0f84', 'jz': '0f84', 'jne': '0f85', 'jnz': '0f85', 'ja': '0f87', 'jbe': '0f86'}

    def __init__(self, va: int):
        self.va0 = va
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fix: list[tuple[int, int, str]] = []       # (position of the displacement, size, label)

    @property
    def va(self) -> int:
        return self.va0 + len(self.buf)

    def __call__(self, *parts):
        for p in parts:
            self.buf += bytes.fromhex(p) if isinstance(p, str) else p
        return self

    def label(self, name: str):
        assert name not in self.labels, name
        self.labels[name] = self.va

    def call(self, target: int):
        return self('e8', i32(target - (self.va + 5)))

    def go(self, op: str, target: int):
        """Near jump/jcc to an absolute address."""
        code = bytes.fromhex(self.NEAR[op])
        return self(code, i32(target - (self.va + len(code) + 4)))

    def j(self, op: str, name: str, near: bool = False):
        """Jump/jcc to a label (short unless near=True)."""
        code = bytes.fromhex((self.NEAR if near else self.SHORT)[op])
        self(code)
        size = 4 if near else 1
        self.fix.append((len(self.buf), size, name))
        self(b'\0' * size)

    def rip(self, prefix: str, target: int, imm: bytes = b''):
        """<prefix> disp32 [imm] with a RIP-relative displacement."""
        n = len(bytes.fromhex(prefix)) + 4 + len(imm)
        return self(prefix, i32(target - (self.va + n)), imm)

    def align(self, n: int, fill: bytes = b'\xcc'):
        while len(self.buf) % n:
            self.buf += fill

    def done(self) -> bytes:
        for pos, size, name in self.fix:
            d = self.labels[name] - (self.va0 + pos + size)
            if size == 1:
                assert -128 <= d < 128, f'short jump to {name} out of range ({d})'
                self.buf[pos] = d & 0xff
            else:
                self.buf[pos:pos + 4] = i32(d)
        return bytes(self.buf)


def find_node(a: Asm, core_reg: str):
    """rax = core->Find(ID_EXIT); core register given as the modrm 'mov rcx, <reg>' + 'mov rax, [<reg>]' bytes."""
    a(core_reg[0])                                  # mov rcx, core
    a('ba', u32(ID_EXIT))                           # mov edx, ID_EXIT
    a(core_reg[1])                                  # mov rax, [core]
    a('ff90', u32(VT_CORE_FIND))                    # call [rax+Find]


def build_n2(dll: bytes) -> codepatch.Target:
    assert codepatch.sha256(dll) in N2_SHAS, 'unknown NarewEngine2 build'
    img = codepatch.PEImage(dll)
    assert img.exports().get(N2_CAVE_SYMBOL) == N2_CAVE - BASE
    patches = []

    def orig(va, n):
        o = img.rva_to_offset(va - BASE, n)
        return dll[o:o + n]

    def add(va, new, note, n=None):
        n = n or len(new)
        new = bytes(new) + b'\xcc' * (n - len(new))
        patches.append(codepatch.Patch(orig(va, n), new, rva=va - BASE, note=note))

    a = Asm(N2_CAVE)
    a(SIG)                                          # signature the IsletEngine side checks
    a.align(16)

    # --- UpdateEvent, root loop start (jumped to; rdi = core, rbx = last root, r12d = elapsed, rsp 16-aligned)
    evt = a.va
    a.rip('488d05', N2_STATE)                       # lea rax, [state]
    a('8b08', '3b4804')                             # mov ecx, [rax+active] ; cmp ecx, [rax+shown]
    a.j('je', 'synced')
    find_node(a, ('4889f9', '488b07'))              # rax = core->Find(exit button)
    a('4885c0')                                     # test rax, rax
    a.j('je', 'synced')                             # not in this core
    a.rip('488d0d', N2_STATE)                       # lea rcx, [state]
    a('8b11', '895104')                             # mov edx, [rcx+active] ; mov [rcx+shown], edx
    a('4889c1', '488b01')                           # mov rcx, rax ; mov rax, [rcx]
    a('ff90', u32(VT_NODE_VISIBLE))                 # exit_button->SetVisible(active)
    a.label('synced')
    a.rip('488d05', N2_STATE)                       # lea rax, [state]
    a('833800')                                     # cmp dword [rax+active], 0
    a.j('jne', 'evt_free')
    a('4885db')                                     # test rbx, rbx      (original code)
    a.go('je', N2_EVT_NOROOT)
    a.go('jmp', N2_EVT_LOOP)
    a.label('evt_free')                             # free camera: only the exit button gets mouse events
    find_node(a, ('4889f9', '488b07'))
    a('4885c0')
    a.j('je', 'evt_done')
    a('4889c1', '4489e2', '488b01')                 # mov rcx, rax ; mov edx, r12d ; mov rax, [rcx]
    a('ff90', u32(VT_NODE_EVENTS))                  # exit_button->UpdateEvent(elapsed)
    a.label('evt_done')
    a.go('jmp', N2_EVT_QUEUE)                       # skip the other roots and the modal node
    a.align(16)

    # --- event dispatch (called): rcx = callback (IGUIFrameWork), rdx = event
    disp = a.va
    a('53', '56', '57', '4883ec20')                 # push rbx ; push rsi ; push rdi ; sub rsp, 0x20
    a('4889ce', '4889d7')                           # mov rsi, rcx ; mov rdi, rdx
    a('4889d1', '488b01', 'ff50', bytes([VT_EVENT_ID]))     # eax = event->GetEvent()
    a('3d', u32(ID_ENTER))                          # cmp eax, ID_ENTER
    a.j('je', 'enter')
    a('3d', u32(ID_EXIT))                           # cmp eax, ID_EXIT
    a.j('je', 'leave')
    a('4889f1', '4889fa', '488b01', 'ff10')         # callback->OnGuiEvent(event)   (the game's handler)
    a.j('jmp', 'disp_out')
    a.label('enter')
    a('bb01000000')                                 # mov ebx, 1
    a.j('jmp', 'set')
    a.label('leave')
    a('31db')                                       # xor ebx, ebx
    a.label('set')
    a.rip('488d05', N2_STATE)                       # lea rax, [state]
    a('8918')                                       # mov [rax+active], ebx
    a.label('disp_out')
    a('4883c420', '5f', '5e', '5b', 'c3')           # add rsp, 0x20 ; pop rdi ; pop rsi ; pop rbx ; ret
    a.align(16)

    # --- Render(pass, draw) root loop (jumped to; rdi = core, rsi = pass, r12d = draw)
    rnd = a.va
    a.rip('488d05', N2_STATE)
    a('833800')                                     # cmp dword [state+active], 0
    a.j('jne', 'rnd_free')
    a('488b8720010000')                             # mov rax, [rdi+0x120]   (original code)
    a.go('jmp', N2_RENDER_LOOP_NEXT)
    a.label('rnd_free')                             # free camera: draw only the exit button
    find_node(a, ('4889f9', '488b07'))
    a('4885c0')
    a.j('je', 'rnd_done')
    a('4889c1', '4889f2', '4589e0', '488b01')       # mov rcx, rax ; mov rdx, rsi ; mov r8d, r12d ; mov rax, [rcx]
    a('ff90', u32(VT_NODE_RENDER))                  # exit_button->Render(pass, draw)
    a.label('rnd_done')
    a.go('jmp', N2_RENDER_DONE)
    code = a.done()
    assert len(code) <= N2_CAVE_SIZE, len(code)
    add(N2_CAVE, code, f'free camera GUI code in the unused {N2_CAVE_SYMBOL}')

    g = Asm(N2_EVT_GATE)
    g.go('jmp', evt)
    add(N2_EVT_GATE, g.done(), 'IGUICore::UpdateEvent: root loop -> free camera filter')
    d = Asm(N2_DISPATCH)
    d('488b13', '488bc8')                           # mov rdx, [rbx] ; mov rcx, rax
    d.call(disp)
    d('90')
    add(N2_DISPATCH, d.done(), 'IGUICore::UpdateEvent: event dispatch -> buttons 31001/31002')
    r = Asm(N2_RENDER_LOOP)
    r.go('jmp', rnd)
    r('6690')
    add(N2_RENDER_LOOP, r.done(), 'IGUICore::Render: root loop -> free camera filter')
    patches.append(codepatch.Patch(u32(N2_DATA_VSIZE), u32(N2_DATA_VSIZE_NEW), rva=N2_DATA_VSIZE_FIELD,
                                   note='.data virtual size to the page end (free camera state block)'))
    patches.sort(key=lambda p: p.rva)
    return codepatch.Target(N2_FILE, patches, codepatch.sha256(dll))


def build_ie(dll: bytes, n2: bytes, speed: float, fast: float) -> codepatch.Target:
    use_ie_build(dll)
    assert codepatch.sha256(n2) in N2_SHAS, 'unknown NarewEngine2 build'
    img = codepatch.PEImage(dll)
    n2img = codepatch.PEImage(n2)
    ex = img.exports()
    assert ex.get(IE_CAVE_SYMBOL) == IE_CAVE - BASE
    for name, symbol in IE_SYMBOLS.items():
        assert ex.get(symbol) == globals()[name] - BASE, name
    anchor_rva = n2img.exports()[ANCHOR_SYMBOL]
    patches = []

    def orig(va, n):
        o = img.rva_to_offset(va - BASE, n)
        return dll[o:o + n]

    def add(va, new, note, n=None):
        n = n or len(new)
        new = bytes(new) + b'\xcc' * (n - len(new))
        patches.append(codepatch.Patch(orig(va, n), new, rva=va - BASE, note=note))

    a = Asm(IE_CAVE)
    # --- rax = NarewEngine2 state block, or 0 when that DLL is not the patched build. Only rax and r11 change.
    get_state = a.va
    a.rip('488b05', IE_ANCHOR_IAT)                  # mov rax, [IAT Nw::Interpolate]
    a('4885c0')
    a.j('je', 'gs_no')
    a('4805', i32((N2_CAVE - BASE) - anchor_rva))   # add rax, cave - Interpolate
    a('49bb', SIG)                                  # mov r11, 'NWFRCAM1'
    a('4c3918')                                     # cmp [rax], r11
    a.j('jne', 'gs_no')
    a('4805', i32(N2_STATE - N2_CAVE))              # add rax, state - cave
    a('c3')
    a.label('gs_no')
    a('31c0', 'c3')                                 # xor eax, eax ; ret
    a.align(16)

    def flag_check(name: str, cmp: str, free_zf: int):
        """ZF as `cmp` would set it, but in free camera mode ZF = free_zf. Keeps every register but rax/r11."""
        at = a.va
        a.call(get_state)
        a('4885c0')
        a.j('je', name + '_o')
        a('833800')                                 # cmp dword [rax+active], 0
        if free_zf:
            a.j('jne', name + '_z')
        else:
            a.j('jne', name + '_r')                 # ZF = 0 already
        a.label(name + '_o')
        a(cmp)
        a.label(name + '_r')
        a('c3')
        if free_zf:
            a.label(name + '_z')
            a('39c0', 'c3')                         # cmp eax, eax (ZF = 1) ; ret
        a.align(16)
        return at

    kb_rcx = flag_check('kbc', '80b96202000000', 0)     # cmp byte [rcx+0x262], 0
    kb_rdi = flag_check('kbd', '80bf6202000000', 0)     # cmp byte [rdi+0x262], 0
    ms_rbx = flag_check('msb', '80bb6302000000', 0)     # cmp byte [rbx+0x263], 0
    auto_rbx = flag_check('aub', '80bb6102000000', 1)   # cmp byte [rbx+0x261], 0
    names = flag_check('nam', '4883b9c001000000', 0)    # cmp qword [rcx+0x1c0], 0  (ZF = 0 -> no name boards)

    # --- speech bubbles: `jle out` after the check; rax holds the caller's rsp there, so keep it
    bubbles = a.va
    a('50')                                         # push rax
    a.call(get_state)
    a('4885c0')
    a.j('je', 'bub_o')
    a('833800')
    a.j('je', 'bub_o')
    a('58', '39c0', 'c3')                           # pop rax ; cmp eax, eax (ZF = 1 -> jle taken) ; ret
    a.label('bub_o')
    a('58', '83b93005000000', 'c3')                 # pop rax ; cmp dword [rcx+0x530], 0 ; ret
    a.align(16)

    # --- damage/number effects: rcx = their list, or 0 in free camera mode (-> `je out`)
    numbers = a.va
    a('488b8900020000')                             # mov rcx, [rcx+0x200]
    a.call(get_state)
    a('4885c0')
    a.j('je', 'num_r')
    a('833800')
    a.j('je', 'num_r')
    a('31c9')                                       # xor ecx, ecx
    a.label('num_r')
    a('c3')
    a.align(16)

    # --- ESC released: leave free camera mode instead of the game's menu callback (ZF = 0 -> skipped)
    esc = a.va
    a('80bf9100000003')                             # cmp byte [rdi+0x91], 3
    a.j('jne', 'esc_r')
    a.call(get_state)
    a('4885c0')
    a.j('je', 'esc_e')
    a('833800')
    a.j('je', 'esc_e')
    a('c70000000000')                               # mov dword [rax+active], 0
    a('4885c0', 'c3')                               # test rax, rax (ZF = 0) ; ret
    a.label('esc_e')
    a('80bf9100000003')                             # cmp byte [rdi+0x91], 3 (ZF = 1)
    a.label('esc_r')
    a('c3')
    a.align(16)

    # --- camera: rcx = CGameCamera, edx = smoothed elapsed ms (called instead of the mode switch)
    cam = a.va
    a('53', '56', '57', '4883ec60')                 # push rbx ; push rsi ; push rdi ; sub rsp, 0x60
    a('0f29742440', '0f297c2450')                   # movaps [rsp+0x40], xmm6 ; movaps [rsp+0x50], xmm7
    a('4889cb', '89d6')                             # mov rbx, rcx ; mov esi, edx
    a.call(get_state)
    a('4885c0')
    a.j('je', 'normal')
    a('4889c7')                                     # mov rdi, rax
    a('833f00')                                     # cmp dword [rdi+active], 0
    a.j('jne', 'free', near=True)
    a('c74708', u32(0))                             # mov dword [rdi+init], 0  (start from the camera next time)
    a.label('normal')                               # the game's own switch
    a('4889d9', '89f2')                             # mov rcx, rbx ; mov edx, esi
    a('8b43', bytes([CAM_MODE]))                    # mov eax, [rbx+mode]
    a('85c0')
    a.j('je', 'fps')
    a('83f801')                                     # cmp eax, 1
    a.j('jne', 'out', near=True)
    a.call(UPDATE_RPG)
    a.j('jmp', 'out', near=True)
    a.label('fps')
    a.call(UPDATE_FPS)
    a.j('jmp', 'out', near=True)

    a.label('free')
    a('4889d9', '89f2', '4531c0')                   # mov rcx, rbx ; mov edx, esi ; xor r8d, r8d
    a('80bb', u32(CAM_LOCKMODE), '00')              # cmp byte [rbx+0x124], 0
    a.j('je', 'rot_r')
    a.call(ROT_LOCK)                                # mouse look (lock mode)
    a.j('jmp', 'rot_done')
    a.label('rot_r')
    a.call(ROT_R)                                   # right drag
    a.label('rot_done')
    a('488b4b', bytes([CAM_NCAM]))                  # mov rcx, [rbx+camera]
    a('4885c9')
    a.j('je', 'out', near=True)
    a('837f', bytes([ST_INIT]), '00')               # cmp dword [rdi+init], 0
    a.j('jne', 'have')
    a('488b41', bytes([NCAM_EYE]), '488947', bytes([ST_POS]))           # pos.xy = eye.xy
    a('8b41', bytes([NCAM_EYE + 8]), '8947', bytes([ST_POS + 8]))       # pos.z = eye.z
    a('c74708', u32(1))                             # mov dword [rdi+init], 1
    a.label('have')
    # front = normalize(at - eye)
    for reg, k in ((0x41, 0), (0x49, 4), (0x51, 8)):    # xmm0/1/2
        a('f30f10', bytes([reg, NCAM_AT + k]))      # movss xmmN, [rcx+at+k]
        a('f30f5c', bytes([reg, NCAM_EYE + k]))     # subss xmmN, [rcx+eye+k]
    a('0f28d8', 'f30f59d8')                         # xmm3 = x*x
    a('0f28e1', 'f30f59e1', 'f30f58dc')             # xmm3 += y*y
    a('0f28e2', 'f30f59e2', 'f30f58dc')             # xmm3 += z*z
    a('f30f51db')                                   # sqrtss xmm3, xmm3
    a('b8', f32(1e-6), '660f6ee0', 'f30f5fdc')      # xmm4 = 1e-6 ; maxss xmm3, xmm4
    a('f30f5ec3', 'f30f5ecb', 'f30f5ed3')           # divss xmm0/1/2, xmm3
    # right = (fz, 0, -fx) / |(fx, fz)|   (left-handed: looking +z, +x is on the right)
    a('0f28d8', 'f30f59d8')                         # xmm3 = fx*fx
    a('0f28ea', 'f30f59ea', 'f30f58dd')             # xmm3 += fz*fz
    a('f30f51db', 'f30f5fdc')                       # sqrtss ; maxss xmm3, 1e-6
    a('0f28ea', 'f30f5eeb')                         # xmm5 = fz / len   (right.x)
    a('0f57e4', 'f30f5ce0', 'f30f5ee3')             # xmm4 = -fx / len  (right.z)
    # keys
    a('4c8b43', bytes([CAM_CTRL]))                  # mov r8, [rbx+control]
    a('4d85c0')
    a.j('je', 'apply', near=True)

    def key(k):                                     # eax = 1 if the key is pressed/held, else 0
        a('410fb640', bytes([KEYS + k]))            # movzx eax, byte [r8+0x5c+k]
        a('ffc8', '83f801', '0f96c0', '0fb6c0')     # dec eax ; cmp eax, 1 ; setbe al ; movzx eax, al

    key(K_FWD), a('4189c1')                         # r9d = W
    key(K_BACK), a('4129c1')                        # r9d -= S
    key(K_RIGHT), a('4189c2')                       # r10d = D
    key(K_LEFT), a('4129c2')                        # r10d -= A
    key(K_JUMP), a('4189c3')                        # r11d = Space
    key(K_SHIFT), a('4129c3')                       # r11d -= Shift
    key(K_CTRL), a('89c1')                          # ecx = Ctrl
    a('f30f2ade')                                   # cvtsi2ss xmm3, esi   (ms)
    a('b8', f32(speed / 1000.0), '660f6ef0', 'f30f59de')    # xmm3 *= speed per ms
    a('85c9')
    a.j('je', 'slow')
    a('b8', f32(fast), '660f6ef0', 'f30f59de')      # xmm3 *= fast
    a.label('slow')
    a('f3410f2af1', 'f3410f2afa')                   # xmm6 = forward axis ; xmm7 = side axis
    a('f30f59c6', 'f30f59ef', 'f30f58c5', 'f30f59c3')   # x = (fx*fwd + rx*side) * step
    a('f30f5847', bytes([ST_POS]), 'f30f1147', bytes([ST_POS]))
    a('f30f59d6', 'f30f59e7', 'f30f58d4', 'f30f59d3')   # z = (fz*fwd + rz*side) * step
    a('f30f5857', bytes([ST_POS + 8]), 'f30f1157', bytes([ST_POS + 8]))
    a('f30f59ce', 'f3410f2aeb', 'f30f58cd', 'f30f59cb')  # y = (fy*fwd + up) * step
    a('f30f584f', bytes([ST_POS + 4]), 'f30f114f', bytes([ST_POS + 4]))
    a.label('apply')
    a('488b4b', bytes([CAM_NCAM]), '488d57', bytes([ST_POS]))   # camera->SetEye(&pos)
    a('488b01', 'ff50', bytes([VT_SETEYE]))
    a('488b4b', bytes([CAM_NCAM]), '488b01', 'ff90', u32(VT_GETROLL))   # xmm0 = camera->GetRoll()
    a('0f28d8')                                     # movaps xmm3, xmm0   (roll)
    a('f30f108b', u32(CAM_YAW), 'f30f1093', u32(CAM_PITCH))     # xmm1 = yaw ; xmm2 = pitch
    a('837b', bytes([CAM_MODE]), '00')              # third person (and selfie) keep orbit angles:
    a.j('je', 'angles')                             #   look = (yaw + 180, 180 - pitch), as SetMode converts
    a('b8', f32(180.0), '660f6ec0')                 # xmm0 = 180
    a('f30f58c8', 'f30f5cc2', '0f28d0')             # yaw += 180 ; xmm0 = 180 - pitch ; xmm2 = xmm0
    a.label('angles')
    a('c7442420', f32(1.0))                         # 5th argument: distance 1
    a('488b4b', bytes([CAM_NCAM]), '488b01', 'ff90', u32(VT_YPR))   # camera->SetYawPitchRoll -> at = eye + front
    a.label('out')
    a('0f28742440', '0f287c2450')                   # restore xmm6, xmm7
    a('4883c460', '5f', '5e', '5b', 'c3')
    code = a.done()
    assert len(code) <= IE_CAVE_SIZE, len(code)
    add(IE_CAVE, code, f'free camera game code in the unused {IE_CAVE_SYMBOL}')

    c = Asm(CAM_DISPATCH)
    c('498bcc')                                     # mov rcx, r12   (edx = smoothed elapsed)
    c.call(cam)
    c.go('jmp', CAM_DISPATCH_END)
    add(CAM_DISPATCH, c.done(), 'CGameCamera::Update: free camera or the mode switch', CAM_DISPATCH_END - CAM_DISPATCH)
    for va, helper, note in ((NM_KB, kb_rcx, 'UpdateNormalMode: keyboard locked in free camera mode'),
                             (NM_AUTO, auto_rbx, 'UpdateNormalMode: no auto move in free camera mode'),
                             (NM_MOUSE, ms_rbx, 'UpdateNormalMode: mouse locked in free camera mode'),
                             (FALL_KB, kb_rdi, 'UpdateFalling: stop walking in free camera mode'),
                             (UPD_ESC, esc, 'CGameControl::Update: ESC leaves free camera mode'),
                             (UPD_KB, kb_rdi, 'CGameControl::Update: keyboard locked in free camera mode'),
                             (BUBBLES, bubbles, 'CSpeechBubbleMgr::Render: no speech bubbles in free camera mode'),
                             (NUMBERS, numbers, 'CNumberEffectMgr::Render: no number effects in free camera mode')):
        h = Asm(va)
        h.call(helper)
        h('6690')
        add(va, h.done(), note)
    h = Asm(NAMES)
    h.call(names)
    h('0f1f00')                                     # 3-byte nop (the check was 8 bytes)
    add(NAMES, h.done(), 'AddDrawName: no name boards in free camera mode')
    patches.sort(key=lambda p: p.rva)
    return codepatch.Target(IE_FILE, patches, codepatch.sha256(dll))


# ---------------------------------------------------------------- GUI
def button_xml(node_id: int, x: int, texture: str, word: str, hidden: bool, label_w: int, indent: str) -> str:
    vis = ' visible ="0"' if hidden else ''
    t = f'''
<node type ="button" touch ="1" id ="{node_id}"{vis}>
  <texture>{texture}</texture>
  <pos x="{x}" y="44" />
  <align x="right" y="top"/>

  <event type ="rollover"/>
  <event type ="mouse_click" id ="{node_id}"></event>

  <node type ="image" visible ="1">
    <align x="center" y="bottom"/>
    <pos x ="0" y ="-3"/>
    <size width ="{label_w}" height ="18"/>
    <texture>03_Minimap\\Bg_Black_temperature.png</texture>

    <node type ="extended_text">
      <pos x ="1" y ="3"/>
      <size width ="128" height ="18"/>
      <align x ="center" />
      <text alignx ="center" aligny ="top">{word}</text>
      <color font ="0xFFFFFFFF" outline ="0xFF000000"></color>
      <font size = "12" >NanumGothicBold.ttf</font>
    </node>
  </node>
</node>
'''
    return '\n'.join((indent + line) if line else '' for line in t.split('\n'))


def add_buttons(xml: str, x: int) -> str:
    """Insert the two buttons as the last children of the layout's root node."""
    if f'id ="{ID_ENTER}"' in xml:
        raise SystemExit('이미 프리캠 버튼이 있는 레이아웃입니다 (원본 파일을 주세요).')
    end = xml.rstrip().rfind('</node>')
    assert end > 0
    nl = '\r\n' if '\r\n' in xml else '\n'
    block = (button_xml(ID_ENTER, x, '02_Mainmenu\\Btn_FreeCam.png', 'MENU_FREECAM', False, 72, '  ')
             + button_xml(ID_EXIT, x, '02_Mainmenu\\Btn_FreeCam_Exit.png', 'MENU_FREECAM_EXIT', True, 88, '  '))
    return xml[:end].rstrip() + nl + block.replace('\n', nl) + xml[end:]


def badge(base, kind: str):
    """Camera button picture with a small badge: 'move' (four arrows) or 'exit' (red x)."""
    from PIL import Image, ImageDraw
    s = 4                                            # draw big, scale down (smooth edges)
    im = base.convert('RGBA').resize((base.width * s, base.height * s), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    cx, cy, r = 47 * s, 47 * s, 14 * s
    if kind == 'move':
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(28, 52, 92, 255), outline=(255, 255, 255, 255), width=2 * s)
        a, w, h = 10 * s, 4 * s, 3 * s
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            tipx, tipy = cx + dx * a, cy + dy * a
            bx, by = cx + dx * (a - h * 2), cy + dy * (a - h * 2)
            px, py = -dy * w, dx * w
            d.polygon([(tipx, tipy), (bx + px, by + py), (bx - px, by - py)], fill=(255, 255, 255, 255))
        d.line((cx - 6 * s, cy, cx + 6 * s, cy), fill=(255, 255, 255, 255), width=2 * s)
        d.line((cx, cy - 6 * s, cx, cy + 6 * s), fill=(255, 255, 255, 255), width=2 * s)
    else:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(205, 40, 40, 255), outline=(255, 255, 255, 255), width=2 * s)
        k = 6 * s
        d.line((cx - k, cy - k, cx + k, cy + k), fill=(255, 255, 255, 255), width=3 * s)
        d.line((cx - k, cy + k, cx + k, cy - k), fill=(255, 255, 255, 255), width=3 * s)
    out = im.resize(base.size, Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, 'PNG')
    return buf.getvalue()


WORDS = '''<merge>
	<match word="MENU_FREECAM" kor="프리캠" eng="Free Cam" cha="自由视角" rus="Свободная камера" thi="กล้องอิสระ" jpn="フリーカメラ" vn="Camera tự do" ph="Free Cam" tw="自由視角" id="Kamera Bebas" es="Cámara libre" pt="Câmera livre" de="Freie Kamera" fr="Caméra libre"/>
	<match word="MENU_FREECAM_EXIT" kor="프리캠 종료" eng="Exit Free Cam" cha="退出自由视角" rus="Выйти" thi="ออกจากกล้องอิสระ" jpn="フリーカメラ終了" vn="Thoát camera tự do" ph="Lumabas" tw="退出自由視角" id="Keluar" es="Salir" pt="Sair" de="Beenden" fr="Quitter"/>
</merge>
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game')
    ap.add_argument('project')
    ap.add_argument('--speed', type=float, default=20.0, help='bricks per second')
    ap.add_argument('--fast', type=float, default=4.0, help='speed factor while Ctrl is held')
    ap.add_argument('--name', default='free_camera')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    def untouched(fname, shas):
        for p in (os.path.join(a.game, fname + '.orig'), os.path.join(a.game, fname)):
            if os.path.isfile(p):
                with open(p, 'rb') as f:
                    data = f.read()
                if codepatch.sha256(data) in shas:
                    print(f'{fname}: {shas[codepatch.sha256(data)]} 빌드')
                    return data
        sys.exit(f'{fname}: 아는 빌드({", ".join(shas.values())})의 원본이 아닙니다 (sha256 불일치).')

    ie = untouched(IE_FILE, {k: v['DATE'] for k, v in IE_BUILDS.items()})
    n2 = untouched(N2_FILE, N2_SHAS)
    t_n2 = build_n2(n2)
    t_ie = build_ie(ie, n2, a.speed, a.fast)
    pf = codepatch.PatchFile(a.name, f'프리캠: 상점 버튼 왼쪽 버튼, WASD·Space·Shift(Ctrl {a.fast:g}배) 이동 '
                             f'{a.speed:g}칸/초, GUI 숨김, ESC·종료 버튼으로 복귀 (NarewEngine2 + IsletEngine)',
                             [t_n2, t_ie])
    codepatch.apply(n2, t_n2)                   # every check (bytes, relocations, overlaps) once more
    codepatch.apply(ie, t_ie)
    text = codepatch.dumps(pf)
    if a.dry_run:
        print(text)
        return

    from studio.game import GameInstall
    from PIL import Image
    game = GameInstall(a.game)
    mod = ModFolder(a.project)
    for name, x in (('Gui\\game_menu_top.xml', -850), ('Gui\\game_menu.xml', -660)):
        raw = game.read(name)
        enc = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
        mod.put(name, add_buttons(raw.decode(enc), x).encode(enc))
    cam = Image.open(io.BytesIO(game.read('gui_skin\\02_Mainmenu\\Btn_Mobile_Camera.png')))
    mod.put('gui_skin\\02_Mainmenu\\Btn_FreeCam.png', badge(cam, 'move'))
    mod.put('gui_skin\\02_Mainmenu\\Btn_FreeCam_Exit.png', badge(cam, 'exit'))
    mod.write_merge('Language\\word.xml', WORDS)
    mod.write_patch(a.name, text)
    print(f'{len(t_n2.patches)} + {len(t_ie.patches)}개 패치 -> {os.path.join(a.project, "patches", a.name + ".json")}')


if __name__ == '__main__':
    main()
