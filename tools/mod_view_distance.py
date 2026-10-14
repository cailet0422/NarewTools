"""Build the "view distance slider" code patch for IsletEngine_release(x64).dll (game build of 2026-10-03).

    python tools/mod_view_distance.py <untouched IsletEngine_release(x64).dll> <mod project folder>
           [--min 20] [--max 512] [--default <level 0..100>] [--dry-run]

What the game does (REA, docs/10-code-patches.md):
  COption keeps the view distance as a level 0..4 (user.xml graphic id 3) and
  COption::UpdateViewDistance turns it into a range in bricks: 70/110/160/190/225 (+ an object range).
  The option window shows "level+1" with < > arrows; three places clamp the level to 4.

What the patch does:
  * the level becomes 0..100 and UpdateViewDistance computes   range  = MIN + level * (MAX - MIN) / 100
                                                               object = min(range, 30 + 0.4 * level)
  * the < > arrows step by 1 and the number next to them shows the range in bricks
  * a slider node (id SLIDER_ID, scroll_event 410, added to Gui\\option.xml by the mod) sets the level;
    opening the window or pressing < > / default moves the slider to the current level
  * new installs start at DEFAULT_LEVEL instead of level 3
  * IBrickGroupManager::UpdateRegionAlloc no longer walks past the end of a short region spiral (ranges under
    32 bricks crashed the game a few frames after the nearby regions were loaded - found in game 2026-10-14)
  * the range also decides how far regions are requested from the server (spiral radius 1 + range/16 regions;
    the local server answers any request), so MAX above the game's 225 loads more of the world; the game camera's
    far plane goes from 300 to 1000 bricks so that is drawn (and the ground is visible from y 255)
  * regions are allocated (and requested) up to ALLOCS per frame instead of one, and the region thread
    (CBrickWorld::Run) handles STEPS regions per run without its Sleep(1)s, so a big range fills in seconds

Room for the new code: UpdateViewDistance's own 144 bytes (new body + slider handler) and the 192 zero
bytes after the end of .text (the section's virtual size is raised to its file size to map them).
The patch is pinned to the DLL's sha256, so a game update simply makes the mod manager skip it.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from narew import codepatch  # noqa: E402
from narew.modfmt import ModFolder  # noqa: E402

SHA256 = '8e4c0cd9ca4f9308db163aefe5f8e14fc3020ab1a1b03a260d3b0955d123e3cf'
BASE = 0x180000000
# --- addresses in that build (VA)
UVD = 0x18012c7d0               # COption::UpdateViewDistance, 0x90 bytes up to the next function
UVD_END = 0x18012c860
OPT_UPDATEDATA_MAX = 0x18012c627    # COption::UpdateData: mov dword [rsp+0x2c], 4   (clamp of graphic[3])
CTOR_DEFAULT = 0x180129fea      # COption::COption: mov dword [rbx+0x1ed8], 3
ON_CHANGED = 0x180131f00        # COptionUI::OnChangedViewRange(int level, int apply)
ON_CHANGED_MAX = 0x180131f22    # mov eax, 4
ON_CHANGED_CALL = 0x180131f73   # mov rcx, rsi ; call UpdateViewDistance
UI_UPDATEDATA_MAX = 0x18013244c     # COptionUI::UpdateData: mov eax, 4
UI_UPDATEDATA_CALL = 0x180132496    # mov rcx, rbx ; call UpdateViewDistance
DEFAULT_MAX = 0x1801332e6       # COptionUI::OnClickDefaultGraphic: mov eax, 4
DEFAULT_CALL = 0x18013332f      # mov rcx, rbx ; call UpdateViewDistance
GUI_SLIDER_JNE = 0x180130fda    # OnGuiEvent, scroll event 410: jne <done> when the node is not 411 (FOV)
GUI_DONE = 0x1801312e8          # OnGuiEvent: common "return 1"
REGION_ALLOC_WRAP = 0x1800aa0a7     # IBrickGroupManager::UpdateRegionAlloc: cursor = 0 ; end = 32 (wrap-around)
CAMERA_FAR_LOAD = 0x1800f8b6d   # CGameCamera::Create: movss xmm1, [300.0] ; call camera->SetFar
REGION_UPDATE_CALL = 0x1800aa02d    # IBrickGroupManager::UpdateRegion: mov rax,[rbx] ; mov edx,edi ; mov rcx,rbx ;
                                    # call [rax+0x68] (= UpdateRegionAlloc, at most one new region per call)
SPARE = 0x180033ba0             # CSky::operator=(const CSky&): exported by the compiler, never called by anything
SPARE_SYMBOL = '??4CSky@Islet@@QEAAAEAV01@AEBV01@@Z'
SPARE_RUN = SPARE + 0x30        # second loop, after the allocation loop
WORLD_RUN_CALLS = 0x1800b6064   # CBrickWorld::Run (region thread): mov rcx,rbx ; call cache-check ; mov rcx,rbx ;
CACHE_STEP, BUILD_STEP = 0x1800a4a60, 0x1800a48f0       # call mesh-build  (16 bytes; each call does ONE region)
STEP_SLEEPS = (0x1800a4b19, 0x1800a4993)    # mov ecx, 1 ; call [Sleep]   after each region in those two steps
F300, F1000 = 0x1802562b8, 0x18025630c      # .rdata floats (1000.0 is the sky camera's far plane)
F100 = 0x1802562ec              # float 100.0 the original slider code multiplies with
WSPRINTFW_IAT = 0x1802081a0     # [IAT] user32!wsprintfW
FMT_D = 0x180238bd8             # L"%d"
TEXT_HDR_VSIZE = 0x218          # file offset of .text VirtualSize in the section table
TEXT_VSIZE, TEXT_RAWSIZE = 0x20433f, 0x204400
SLACK = 0x180205340             # first 16-aligned byte after .text's virtual end
SLACK_END = 0x180205400
# COptionUI / COption members
UI_GUI_ROOT, UI_OPTION, UI_RANGE_TEXT = 0x20, 0x50, 0x160
OPT_LEVEL, OPT_RANGE, OPT_OBJ_RANGE = 0x1ed8, 0x1f50, 0x1f54
VT_FIND_NODE, VT_SET_TEXT, VT_SLIDER_GET, VT_SLIDER_SET = 0xe0, 0x388, 0x420, 0x428


def f32(x: float) -> bytes:
    return struct.pack('<f', x)


def rel32(at_next: int, target: int) -> bytes:
    return struct.pack('<i', target - at_next)


class Asm:
    """Tiny label-aware byte emitter (the instructions are spelled out by hand below)."""

    def __init__(self, va: int):
        self.va0 = va
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fix8: list[tuple[int, str]] = []

    @property
    def va(self) -> int:
        return self.va0 + len(self.buf)

    def __call__(self, *parts):
        for p in parts:
            self.buf += bytes.fromhex(p) if isinstance(p, str) else p
        return self

    def label(self, name):
        self.labels[name] = self.va

    def call(self, target):          # E8 rel32
        return self('e8', rel32(self.va + 5, target))

    def jmp(self, target):           # E9 rel32
        return self('e9', rel32(self.va + 5, target))

    def jne32(self, target):         # 0F 85 rel32
        return self('0f85', rel32(self.va + 6, target))

    def jz8(self, name):             # 74 rel8 (forward)
        self('74 00')
        self.fix8.append((len(self.buf) - 1, name))

    def ripdisp(self, prefix: str, target: int, extra: int = 0):
        """<prefix> disp32 where disp is RIP-relative to the end of the instruction (+extra imm bytes)."""
        n = len(bytes.fromhex(prefix)) + 4 + extra
        return self(prefix, rel32(self.va + n, target))

    def done(self) -> bytes:
        for pos, name in self.fix8:
            d = self.labels[name] - (self.va0 + pos + 1)
            assert -128 <= d < 128, name
            self.buf[pos] = d & 0xff
        return bytes(self.buf)


def build(dll: bytes, vmin: float, vmax: float, default: int, slider_id: int, allocs: int = 32,
          steps: int = 16) -> codepatch.Target:
    img = codepatch.PEImage(dll)

    def orig(va, n):
        o = img.rva_to_offset(va - BASE, n)
        return dll[o:o + n]

    step = (vmax - vmin) / 100.0
    patches = []

    def add(va, new, note, n=None):
        n = n or len(new)
        new = bytes(new) + b'\xcc' * (n - len(new))
        patches.append(codepatch.Patch(orig(va, n), new, rva=va - BASE, note=note))

    # 1) UpdateViewDistance: linear range from the 0..100 level, then the slider handler behind it
    a = Asm(UVD)
    a('8b81', struct.pack('<I', OPT_LEVEL))             # mov eax, [rcx+level]
    a('f30f2ac0')                                       # cvtsi2ss xmm0, eax
    a('b8', f32(step), '660f6ec8')                      # mov eax, step ; movd xmm1, eax
    a('f30f59c8')                                       # mulss xmm1, xmm0
    a('b8', f32(vmin), '660f6ed0')                      # mov eax, min ; movd xmm2, eax
    a('f30f58d1')                                       # addss xmm2, xmm1
    a('f30f1191', struct.pack('<I', OPT_RANGE))         # movss [rcx+range], xmm2
    a('b8', f32(0.4), '660f6ec8')                       # mov eax, 0.4 ; movd xmm1, eax
    a('f30f59c8')                                       # mulss xmm1, xmm0
    a('b8', f32(30.0), '660f6ed8')                      # mov eax, 30 ; movd xmm3, eax
    a('f30f58cb')                                       # addss xmm1, xmm3
    a('f30f5dca')                                       # minss xmm1, xmm2
    a('f30f1189', struct.pack('<I', OPT_OBJ_RANGE))     # movss [rcx+obj], xmm1
    a('c3')
    # slider handler, entered from OnGuiEvent (scroll event 410) with esi = node id - 411,
    # rdi = the slider node, rbx = this + 0x10; same frame and stack alignment as the FOV case
    handler = a.va
    a('81fe', struct.pack('<i', slider_id - 0x19b))     # cmp esi, id - 411
    a.jne32(GUI_DONE)
    a('488b07', '488bcf', 'ff90', struct.pack('<I', VT_SLIDER_GET))  # value = node->GetValue()
    a('488d4bf0')                                       # lea rcx, [rbx-0x10]   (COptionUI*)
    a.ripdisp('f30f5905', F100)                         # mulss xmm0, [100.0]
    a('f30f2cd0')                                       # cvttss2si edx, xmm0
    a('41b801000000')                                   # mov r8d, 1
    a.call(ON_CHANGED)                                  # OnChangedViewRange(level, 1)
    a.jmp(GUI_DONE)
    body = a.done()
    assert len(body) <= UVD_END - UVD, len(body)
    add(UVD, body, 'UpdateViewDistance: range = min + level*step; + slider handler', UVD_END - UVD)

    # 2) shared helper (in .text slack): UpdateViewDistance + range text + slider position. rcx = COptionUI*
    s = Asm(SLACK)
    s('53', '56', '4883ec48')                           # push rbx ; push rsi ; sub rsp, 0x48
    s('4889cb')                                         # mov rbx, rcx
    s('488b4b', bytes([UI_OPTION]))                     # mov rcx, [rbx+option]
    s.call(UVD)
    s('488b43', bytes([UI_OPTION]))                     # mov rax, [rbx+option]
    s('f3440f2c80', struct.pack('<I', OPT_RANGE))       # cvttss2si r8d, [rax+range]
    s.ripdisp('488d15', FMT_D)                          # lea rdx, [L"%d"]
    s('488d4c2420')                                     # lea rcx, [rsp+0x20]
    s.ripdisp('ff15', WSPRINTFW_IAT)                    # call [wsprintfW]
    s('488b8b', struct.pack('<I', UI_RANGE_TEXT))       # mov rcx, [rbx+range text node]
    s('488d542420', '4533c0', '488b01')                 # lea rdx, [rsp+0x20] ; xor r8d, r8d ; mov rax, [rcx]
    s('ff90', struct.pack('<I', VT_SET_TEXT))           # call [rax+SetText]
    s('488b4b', bytes([UI_GUI_ROOT]))                   # mov rcx, [rbx+gui root]
    s('4885c9')                                         # test rcx, rcx
    s.jz8('out')
    s('ba', struct.pack('<I', slider_id))               # mov edx, slider id
    s('488b01', 'ff90', struct.pack('<I', VT_FIND_NODE))    # rax = root->FindNode(id)
    s('4885c0')                                         # test rax, rax
    s.jz8('out')
    s('4889c6', '4889c1', '488b01')                     # mov rsi, rax ; mov rcx, rax ; mov rax, [rcx]
    s('ff90', struct.pack('<I', VT_SLIDER_GET))         # xmm0 = slider->GetValue()
    s.ripdisp('f30f5905', F100)                         # mulss xmm0, [100.0]
    s('f30f2cc0')                                       # cvttss2si eax, xmm0
    s('488b53', bytes([UI_OPTION]))                     # mov rdx, [rbx+option]
    s('3b82', struct.pack('<I', OPT_LEVEL))             # cmp eax, [rdx+level]
    s.jz8('out')                                        # already there (also ends slider -> handler -> here)
    s('f30f2a8a', struct.pack('<I', OPT_LEVEL))         # cvtsi2ss xmm1, [rdx+level]
    s('b8', f32(0.5), '660f6ec0', 'f30f58c8')           # xmm1 += 0.5
    s.ripdisp('f30f5e0d', F100)                         # divss xmm1, [100.0]
    s('4889f1', '488b01')                               # mov rcx, rsi ; mov rax, [rcx]
    s('ff90', struct.pack('<I', VT_SLIDER_SET))         # slider->SetValue(xmm1)
    s.label('out')
    s('4883c448', '5e', '5b', 'c3')                     # add rsp, 0x48 ; pop rsi ; pop rbx ; ret
    helper = s.done()
    assert len(helper) <= SLACK_END - SLACK, len(helper)
    add(SLACK, helper, 'helper after .text: UpdateViewDistance, range text, slider position')
    patches.append(codepatch.Patch(struct.pack('<I', TEXT_VSIZE), struct.pack('<I', TEXT_RAWSIZE),
                                   rva=TEXT_HDR_VSIZE, note='.text virtual size = file size (maps the helper)'))

    # 3) the three UI places: clamp 4 -> 100, and call the helper with the window instead of UpdateViewDistance
    for va_max, va_call, who in ((ON_CHANGED_MAX, ON_CHANGED_CALL, 'OnChangedViewRange'),
                                 (UI_UPDATEDATA_MAX, UI_UPDATEDATA_CALL, 'COptionUI::UpdateData'),
                                 (DEFAULT_MAX, DEFAULT_CALL, 'OnClickDefaultGraphic')):
        add(va_max, bytes.fromhex('b864000000'), f'{who}: level up to 100')
        c = Asm(va_call)
        c('488bcf')                                     # mov rcx, rdi   (the window)
        c.call(SLACK)
        add(va_call, c.done(), f'{who}: helper instead of UpdateViewDistance')

    # 4) loading user.xml clamps graphic[3] to 100; a new install starts at the default level
    add(OPT_UPDATEDATA_MAX, bytes.fromhex('c744242c') + struct.pack('<i', 100), 'COption::UpdateData: level up to 100')
    add(CTOR_DEFAULT, bytes.fromhex('c783') + struct.pack('<Ii', OPT_LEVEL, default), 'COption: default level')

    # 5) OnGuiEvent: a slider event whose node is not FOV goes to the handler instead of "return 1"
    j = Asm(GUI_SLIDER_JNE)
    j.jne32(handler)
    add(GUI_SLIDER_JNE, j.done(), 'OnGuiEvent: scroll 410 from another node -> slider handler')

    # 6) UpdateRegionAlloc walks the region spiral 32 points per frame; on wrap-around it walks 0..31 even when
    #    the spiral is shorter. The spiral has (1 + range/16)^2 * 4 points: 16 below 32 bricks, which the game's
    #    own minimum (70) never reached -> GetPoint(17) = NULL -> crash. End the walk at min(32, count).
    w = Asm(REGION_ALLOC_WRAP)
    w('83676000')                                       # and dword [rdi+0x60], 0   (cursor = 0)
    w('6a20', '5b')                                     # push 0x20 ; pop rbx         (end = 32)
    w('3bc3', '0f4cd8')                                 # cmp eax, ebx ; cmovl ebx, eax   (eax = count)
    add(REGION_ALLOC_WRAP, w.done(), 'UpdateRegionAlloc: wrap-around walk ends at min(32, spiral count)')

    # 7) the game camera's projection ends at 300 bricks (CBrickWorld::BuildSceneGraph only narrows it to
    #    range + 27.7 for culling and puts it back). Point its SetFar at the 1000.0 the sky camera uses, so ranges
    #    past ~270 and the ground seen from the height limit (y 255) are drawn.
    assert struct.unpack('<f', orig(F300, 4))[0] == 300.0 and struct.unpack('<f', orig(F1000, 4))[0] == 1000.0
    c = Asm(CAMERA_FAR_LOAD)
    c.ripdisp('f30f100d', F1000)                        # movss xmm1, [1000.0]
    add(CAMERA_FAR_LOAD, c.done(), 'CGameCamera::Create: far plane 300 -> 1000')

    # 8) faster loading: UpdateRegion calls UpdateRegionAlloc once a frame and that adds at most one region, so a
    #    big range took minutes to fill. Call it `allocs` times instead (each call resumes at its saved cursor).
    #    The loop lives in CSky::operator=, a compiler-made export nothing calls.
    assert img.exports().get(SPARE_SYMBOL) == SPARE - BASE and 1 <= allocs <= 64
    m = Asm(SPARE)
    m('53', '56', '57', '4883ec20')                     # push rbx ; push rsi ; push rdi ; sub rsp, 0x20
    m('4889cb', '89d7')                                 # mov rbx, rcx (manager) ; mov edi, edx (elapsed ms)
    m('be', struct.pack('<I', allocs))                  # mov esi, allocs
    m.label('again')
    m('4889d9', '89fa', '488b03', 'ff5068')             # rcx = rbx ; edx = edi ; rax = vtable ; UpdateRegionAlloc
    m('ffce')                                           # dec esi
    m('75', bytes([(m.labels['again'] - (m.va + 2)) & 0xff]))     # jnz again
    m('4883c420', '5f', '5e', '5b', 'c3')               # add rsp, 0x20 ; pop rdi ; pop rsi ; pop rbx ; ret
    add(SPARE, m.done(), f'UpdateRegionAlloc x{allocs} per frame (in the unused CSky::operator=)')
    u = Asm(REGION_UPDATE_CALL)
    u('488bcb', '8bd7')                                 # mov rcx, rbx ; mov edx, edi
    u.call(SPARE)
    u('90')
    add(REGION_UPDATE_CALL, u.done(), f'UpdateRegion: allocate up to {allocs} regions a frame')

    # 9) the region thread (Nw thread loop: Run(); Sleep(2)) cache-checks one region and builds one region mesh per
    #    Run, each followed by Sleep(1) - a few dozen regions a second. Let Run do `steps` of each (the steps return
    #    at once when their queue is empty) and turn those Sleep(1) into Sleep(0) (yield, no wait).
    assert len(m.buf) <= SPARE_RUN - SPARE and 1 <= steps <= 256
    r = Asm(SPARE_RUN)
    r('56', '4883ec20')                                 # push rsi ; sub rsp, 0x20   (rbx = cache, kept by Run)
    r('be', struct.pack('<I', steps))                   # mov esi, steps
    r.label('step')
    r('4889d9')                                         # mov rcx, rbx
    r.call(CACHE_STEP)
    r('4889d9')                                         # mov rcx, rbx
    r.call(BUILD_STEP)
    r('ffce')                                           # dec esi
    r('75', bytes([(r.labels['step'] - (r.va + 2)) & 0xff]))      # jnz step
    r('4883c420', '5e', 'c3')                           # add rsp, 0x20 ; pop rsi ; ret
    add(SPARE_RUN, r.done(), f'region thread: {steps} cache checks + {steps} mesh builds per Run')
    w2 = Asm(WORLD_RUN_CALLS)
    w2.call(SPARE_RUN)
    w2('90' * 11)
    add(WORLD_RUN_CALLS, w2.done(), 'CBrickWorld::Run: call the region loop instead of one step each')
    for va in STEP_SLEEPS:
        add(va, bytes.fromhex('b900000000'), 'region thread: Sleep(1) -> Sleep(0) after each region')

    patches.sort(key=lambda p: p.rva)
    return codepatch.Target('IsletEngine_release(x64).dll', patches, SHA256)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dll')
    ap.add_argument('project')
    ap.add_argument('--min', type=float, default=20.0)
    ap.add_argument('--max', type=float, default=512.0)
    ap.add_argument('--default', type=int, default=None, help='level 0..100 for new installs (default: ~190 bricks)')
    ap.add_argument('--slider-id', type=int, default=4100)
    ap.add_argument('--allocs', type=int, default=32, help='regions allocated per frame (game: 1)')
    ap.add_argument('--steps', type=int, default=16, help='regions the region thread handles per run (game: 1)')
    ap.add_argument('--name', default='view_distance_slider')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    with open(a.dll, 'rb') as f:
        dll = f.read()
    if codepatch.sha256(dll) != SHA256:
        sys.exit('이 스크립트는 2026-10-03 빌드의 IsletEngine_release(x64).dll 전용입니다 (sha256 불일치).')
    if a.default is None:
        a.default = round((190.0 - a.min) * 100 / (a.max - a.min))
    t = build(dll, a.min, a.max, a.default, a.slider_id, a.allocs, a.steps)
    pf = codepatch.PatchFile(a.name, f'시야거리·영역 로딩 슬라이더 ({a.min:g}~{a.max:g}칸, 기본 '
                             f'{a.min + a.default * (a.max - a.min) / 100:.0f}칸, 카메라 원거리 1000칸, '
                             f'영역 할당 프레임당 {a.allocs}개, 영역 스레드 한 번에 {a.steps}개)', [t])
    codepatch.apply(dll, t)                 # every check (bytes, relocations, overlaps) once more
    text = codepatch.dumps(pf)
    if a.dry_run:
        print(text)
        return
    ModFolder(a.project).write_patch(a.name, text)
    print(f'{len(t.patches)}개 패치 -> {os.path.join(a.project, "patches", a.name + ".json")}')


if __name__ == '__main__':
    main()
