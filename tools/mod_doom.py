"""Build the "DOOM on the character select screen" mod for the 64-bit client (NarewEngine2 of 2026-10-03 or -16).

    python native/narewdoom/build.py                       (once: builds native/narewdoom/build/NarewDoom.dll)
    python tools/mod_doom.py <game folder> <mod project folder> --wad <doom1.wad> [--dll NarewDoom.dll] [--dry-run]

The game folder is only read: the untouched NarewEngine2_Release64.dll (``.orig`` when the mod manager already
patched it) and Gui\\character_select.xml.

What the mod does:
  * a 648x408 picture on the left of the character select screen runs DOOM (doomgeneric, the IWAD from --wad) with
    sound effects and music; it pauses (picture, clock, sound) whenever the screen is not shown
  * a click on the picture gives DOOM the keyboard (red frame): arrows move/turn, Ctrl fire, Space use/open,
    Shift run, Alt strafe, , . sidestep, Esc menu, Enter choose, 1-7 weapons, Tab map.  While it has the keyboard the
    game sees no keys (no ESC "quit the game?" window).  A click anywhere else gives the keyboard back (grey frame)
  * DOOM's settings and savegames: Documents\\NarewStudio\\NarewDoom\\ (also narewdoom.log); "Quit Game" in DOOM goes back
    to its title screen.  Another IWAD dropped into <game>\\NarewDoom\\ (doom.wad, doom2.wad, ...) is preferred

How (see docs/10-code-patches.md, native/narewdoom/narewdoom.c):
  NarewEngine2_Release64.dll
    - Nw::IGUICore::Render() start: jump to new code that loads NarewDoom.dll from the game folder once
      (LoadLibraryW + GetProcAddress through the DLL's own imports) and calls NarewDoom_Frame(core, &focus)
    - Nw::IKeyboard::Update: its GetAsyncKeyState call goes through a helper that returns "not pressed" while focus
      is set (the game's keyboard polling; the mouse is untouched)
    - new code in the unused compiler-made export IGUICore::operator= (the free camera mod uses IGUICore's copy
      constructor, so both fit together); 16 bytes of state in the unused tail of the .data page at 0x180755f00
      (the free camera state sits at 0x180755c00)
  NarewDoom.dll does the rest: finds image node 31100 (added to Gui\\character_select.xml by this script) through
  IGUICore::Find, runs DOOM in its own thread and copies each frame into that node's D3D9 texture.
Loose files: NarewDoom.dll (game folder), NarewDoom\\<iwad>, NarewDoom\\COPYING.txt (GPL + where the source is).
The patch is pinned to the NarewEngine2 sha256; NarewDoom.dll checks the same build before touching anything.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from narew import codepatch  # noqa: E402
from narew.modfmt import ModFolder  # noqa: E402
from mod_freecam import Asm, i32, u32  # noqa: E402  (same hand assembler)

BASE = 0x180000000
NODE_ID = 31100                             # = DOOM_NODE_ID in narewdoom.c
DLL_NAME = 'NarewDoom.dll'
PROC_NAME = 'NarewDoom_Frame'
TEX_W, TEX_H = 648, 408                     # 640x400 DOOM + 4 px frame (narewdoom.c TEX_W/TEX_H)
TEXTURE = 'NarewDoom\\doom_screen.png'      # gui_skin entry (gui_skin\NarewDoom\doom_screen.png)

N2_FILE = 'NarewEngine2_Release64.dll'
# known NarewEngine2 builds; the Steam update of 2026-10-16 rebuilt the DLL with byte-identical code (only the PE/debug
# timestamps and the PDB age differ), so the addresses below hold for both.  The patch is pinned to the one found.
N2_SHAS = {
    '40d2a2fc50b3cc3e235ca2afcdc18cfe1f679c921528fecbc15a2191b6adb23c': '2026-10-03',
    '3ce895c96dbadc050f4e3d34d8bc85861fcea15a67da32efc70055f5c17ee288': '2026-10-16',
}
N2_CAVE = 0x1800251b0                       # ??4IGUICore@Nw@@QEAAAEAV01@AEBV01@@Z (1287 bytes, never called)
N2_CAVE_SYMBOL = '??4IGUICore@Nw@@QEAAAEAV01@AEBV01@@Z'
N2_CAVE_SIZE = 1287
RENDER = 0x180032930                        # IGUICore::Render(): push rbx ; sub rsp,20h ; mov rbx,rcx  (9 bytes)
RENDER_SYMBOL = '?Render@IGUICore@Nw@@UEAAXXZ'
RENDER_ORIG = bytes.fromhex('405348' '83ec20' '488bd9')
RENDER_NEXT = 0x180032939                   # mov rcx,[rcx+130h]
KEYS_CALL = 0x18014710c                     # IKeyboard::Update: call [rip+..] GetAsyncKeyState  (6 bytes)
KEYS_SYMBOL = '?Update@IKeyboard@Nw@@UEAAXK@Z'
IAT_LOADLIBRARYW = 0x18055c300
IAT_GETPROCADDRESS = 0x18055c400
IAT_GETASYNCKEYSTATE = 0x18055cbf0
STATE = 0x180755f00                         # .data page tail: +0 focus (int), +8 NarewDoom_Frame, +0x10 tried
ST_FOCUS, ST_PROC, ST_TRIED = STATE, STATE + 8, STATE + 0x10
DATA_END, PAGE_END = 0x1807558e8, 0x180756000


def iat_slot(img: codepatch.PEImage, dll: str, func: str) -> int:
    """VA of the import address table slot of dll!func (checks the hard-coded addresses above)."""
    rva, _ = img.dirs[1]
    d = img.data
    o = img.rva_to_offset(rva, 20)
    while True:
        oft, _, _, name_rva, ft = struct.unpack_from('<IIIII', d, o)
        if not name_rva:
            break
        n = img.rva_to_offset(name_rva)
        if d[n:d.index(b'\0', n)].decode().lower() == dll.lower():
            k = 0
            while True:
                t, = struct.unpack_from('<Q', d, img.rva_to_offset((oft or ft) + 8 * k, 8))
                if not t:
                    break
                if not t >> 63:
                    h = img.rva_to_offset(t & 0x7fffffff) + 2
                    if d[h:d.index(b'\0', h)].decode() == func:
                        return BASE + ft + 8 * k
                k += 1
        o += 20
    raise SystemExit(f'{dll}!{func} import가 없습니다')


def build_n2(dll: bytes) -> codepatch.Target:
    assert codepatch.sha256(dll) in N2_SHAS, 'unknown NarewEngine2 build'
    img = codepatch.PEImage(dll)
    ex = img.exports()
    assert ex.get(N2_CAVE_SYMBOL) == N2_CAVE - BASE
    assert ex.get(RENDER_SYMBOL) == RENDER - BASE
    assert BASE + ex[KEYS_SYMBOL] < KEYS_CALL < BASE + ex[KEYS_SYMBOL] + 0x110
    assert iat_slot(img, 'KERNEL32.dll', 'LoadLibraryW') == IAT_LOADLIBRARYW
    assert iat_slot(img, 'KERNEL32.dll', 'GetProcAddress') == IAT_GETPROCADDRESS
    assert iat_slot(img, 'USER32.dll', 'GetAsyncKeyState') == IAT_GETASYNCKEYSTATE
    data = [s for s in img.sections if s[0] == '.data'][0]
    assert BASE + data[1] + data[2] == DATA_END and DATA_END <= STATE and ST_TRIED + 4 <= PAGE_END
    patches = []

    def orig(va, n):
        o = img.rva_to_offset(va - BASE, n)
        return dll[o:o + n]

    def add(va, new, note, n=None):
        n = n or len(new)
        new = bytes(new) + b'\xcc' * (n - len(new))
        patches.append(codepatch.Patch(orig(va, n), new, rva=va - BASE, note=note))

    assert orig(RENDER, 9) == RENDER_ORIG
    assert orig(KEYS_CALL, 2) == b'\xff\x15' and \
        KEYS_CALL + 6 + struct.unpack('<i', orig(KEYS_CALL + 2, 4))[0] == IAT_GETASYNCKEYSTATE

    a = Asm(N2_CAVE)
    a(b'NWDOOM01')                                  # marker (nothing reads it; shows up in a hex view)
    dllname = a.va
    a(DLL_NAME.encode('utf-16-le') + b'\0\0')
    procname = a.va
    a(PROC_NAME.encode('ascii') + b'\0')
    a.align(16)

    # --- NarewDoom_Frame(core, &focus), loading the DLL once (rcx = core; keeps rbx/rsi/rdi and the rest
    #     the Windows x64 ABI says a callee keeps)
    frame = a.va
    a('56', '4883ec20')                             # push rsi ; sub rsp, 20h      (rsp 16-aligned)
    a('4889ce')                                     # mov rsi, rcx
    a.rip('488b05', ST_PROC)                        # mov rax, [proc]
    a('4885c0')
    a.j('jne', 'call')
    a.rip('833d', ST_TRIED, b'\x00')                # cmp dword [tried], 0
    a.j('jne', 'out')
    a.rip('c705', ST_TRIED, u32(1))                 # mov dword [tried], 1   (a missing DLL is tried only once)
    a.rip('488d0d', dllname)                        # lea rcx, [L"NarewDoom.dll"]
    a.rip('ff15', IAT_LOADLIBRARYW)                 # call [LoadLibraryW]   (game folder first)
    a('4885c0')
    a.j('je', 'out')
    a('4889c1')                                     # mov rcx, rax
    a.rip('488d15', procname)                       # lea rdx, ["NarewDoom_Frame"]
    a.rip('ff15', IAT_GETPROCADDRESS)               # call [GetProcAddress]
    a('4885c0')
    a.j('je', 'out')
    a.rip('488905', ST_PROC)                        # mov [proc], rax
    a.label('call')
    a('4889f1')                                     # mov rcx, rsi
    a.rip('488d15', ST_FOCUS)                       # lea rdx, [focus]
    a('ffd0')                                       # call rax
    a.label('out')
    a('4883c420', '5e', 'c3')                       # add rsp, 20h ; pop rsi ; ret
    a.align(16)

    # --- IGUICore::Render() start (jumped to; rcx = core): the original prologue, DOOM, back
    render = a.va
    a('4053', '4883ec20', '488bd9')                 # push rbx ; sub rsp, 20h ; mov rbx, rcx   (as the original)
    a.call(frame)
    a('4889d9')                                     # mov rcx, rbx
    a.go('jmp', RENDER_NEXT)
    a.align(16)

    # --- GetAsyncKeyState for IKeyboard::Update (called; ecx = virtual key): no key while DOOM has the keyboard
    keys = a.va
    a.rip('833d', ST_FOCUS, b'\x00')                # cmp dword [focus], 0
    a.j('jne', 'nokey')
    a.rip('ff25', IAT_GETASYNCKEYSTATE)             # jmp [GetAsyncKeyState]
    a.label('nokey')
    a('31c0', 'c3')                                 # xor eax, eax ; ret
    code = a.done()
    assert len(code) <= N2_CAVE_SIZE, len(code)
    add(N2_CAVE, code, f'DOOM loader / frame call / keyboard gate in the unused {N2_CAVE_SYMBOL}')

    r = Asm(RENDER)
    r.go('jmp', render)
    add(RENDER, r.done(), 'IGUICore::Render(): call NarewDoom_Frame first', 9)
    k = Asm(KEYS_CALL)
    k.call(keys)
    k('90')
    add(KEYS_CALL, k.done(), 'IKeyboard::Update: GetAsyncKeyState -> 0 while DOOM has the keyboard')
    patches.sort(key=lambda p: p.rva)
    return codepatch.Target(N2_FILE, patches, codepatch.sha256(dll))


# ---------------------------------------------------------------- GUI
NODE_XML = '''
<node type ="image" id ="{id}" visible ="1" desc ="NarewDoom">
  <texture>{tex}</texture>
  <size width ="{w}" height ="{h}"/>
  <pos x ="{x}" y ="{y}"/>
  <align x ="left" y ="center"/>

  <node type ="extended_text">
    <pos x ="0" y ="{hy}"/>
    <size width ="{w}" height ="22"/>
    <align x ="center" y ="top"/>
    <text alignx ="center" aligny ="center">NAREWDOOM_HINT</text>
    <color font ="0xFFFFFFFF" outline ="0xFF000000"/>
    <font size = "13" >NanumGothicBold.ttf</font>
  </node>
</node>
'''


def first_node_end(xml: str) -> int:
    """Index just after the </node> that closes the first top-level <node> under <root>."""
    i = xml.index('<node', xml.index('<root>'))
    depth = 0
    for m in re.finditer(r'<node\b[^>]*>|</node\s*>', xml[i:]):
        tok = m.group()
        if tok.startswith('</'):
            depth -= 1
        elif not tok.endswith('/>'):            # <node .../> opens and closes at once
            depth += 1
        if depth == 0:
            return i + m.end()
    raise SystemExit('레이아웃의 첫 노드 끝을 찾지 못했습니다')


def add_node(xml: str, x: int, y: int) -> str:
    """The DOOM picture as the SECOND top-level node: the client takes IGUIParser::GetFirstNode() of
    character_select.xml as the character panel and looks up buttons 60/61/62 in it (CCharacterSelect, exe
    0x1400b30f2), so the first node must stay first.  Second = drawn above the panel, under the popups."""
    if f'id ="{NODE_ID}"' in xml:
        raise SystemExit('이미 DOOM 노드가 있는 레이아웃입니다 (원본 파일을 주세요).')
    at = first_node_end(xml)
    nl = '\r\n' if '\r\n' in xml else '\n'
    block = NODE_XML.format(id=NODE_ID, tex=TEXTURE, w=TEX_W, h=TEX_H, x=x, y=y, hy=TEX_H + 4)
    block = '\n'.join(('  ' + ln) if ln else '' for ln in block.split('\n'))
    return xml[:at] + block.replace('\n', nl) + xml[at:]


def placeholder_png() -> bytes:
    """What the picture shows until DOOM's first frame (and stays when DOOM cannot start)."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new('RGBA', (TEX_W, TEX_H), (0, 0, 0, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, TEX_W - 1, TEX_H - 1), outline=(58, 58, 58, 255), width=4)

    def font(size):
        for f in ('malgunbd.ttf', 'malgun.ttf', 'arialbd.ttf'):
            try:
                return ImageFont.truetype(os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', f), size)
            except OSError:
                continue
        return ImageFont.load_default()

    for text, size, y, color in (('DOOM', 96, 100, (200, 30, 30, 255)),
                                 ('불러오는 중…', 26, 226, (220, 220, 220, 255)),
                                 ('계속 이 그림이면 확인하세요:', 17, 288, (150, 150, 150, 255)),
                                 ('게임 폴더의 NarewDoom.dll, NarewDoom\\doom1.wad', 17, 314, (150, 150, 150, 255)),
                                 ('로그: 문서\\NarewStudio\\NarewDoom\\narewdoom.log', 17, 340, (150, 150, 150, 255))):
        f = font(size)
        w = d.textlength(text, font=f)
        d.text(((TEX_W - w) / 2, y), text, font=f, fill=color)
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return buf.getvalue()


WORDS = '''<merge>
	<match word="NAREWDOOM_HINT" kor="화면을 클릭하면 DOOM 조작 · 방향키 이동 · Ctrl 발사 · Space 열기 · Esc 메뉴" eng="Click the screen to play DOOM · arrows move · Ctrl fire · Space use · Esc menu" cha="点击画面玩 DOOM · 方向键移动 · Ctrl 开火 · Space 使用 · Esc 菜单" rus="Щёлкните по экрану, чтобы играть в DOOM · стрелки · Ctrl огонь · Space · Esc меню" thi="คลิกหน้าจอเพื่อเล่น DOOM · ลูกศร เดิน · Ctrl ยิง · Space ใช้ · Esc เมนู" jpn="画面をクリックで DOOM を操作 · 矢印 移動 · Ctrl 射撃 · Space 使う · Esc メニュー" vn="Nhấp vào màn hình để chơi DOOM · mũi tên · Ctrl bắn · Space dùng · Esc menu" ph="Click the screen to play DOOM · arrows move · Ctrl fire · Space use · Esc menu" tw="點擊畫面玩 DOOM · 方向鍵移動 · Ctrl 開火 · Space 使用 · Esc 選單" id="Klik layar untuk main DOOM · panah gerak · Ctrl tembak · Space pakai · Esc menu" es="Clic en la pantalla para jugar DOOM · flechas · Ctrl disparar · Espacio usar · Esc menú" pt="Clique na tela para jogar DOOM · setas · Ctrl atirar · Espaço usar · Esc menu" de="Zum Spielen auf DOOM klicken · Pfeile · Strg feuern · Leertaste benutzen · Esc Menü" fr="Cliquez sur l’écran pour jouer à DOOM · flèches · Ctrl tirer · Espace utiliser · Échap menu"/>
</merge>
'''

COPYING = '''NarewDoom - DOOM on the Islet Online character select screen (NarewTools mod)

NarewDoom.dll is doomgeneric (https://github.com/ozkl/doomgeneric, commit {commit}), which is based on
Chocolate Doom and id Software's DOOM source release, plus the NarewTools glue code (narewdoom.c,
narewdoom_sound.c).  It is free software under the GNU General Public License version 2 or later; the full
license text follows below.  The complete source code of this build is in the NarewTools repository,
folder native/narewdoom (https://github.com/cailet0422/NarewTools), and can also be had from whoever gave
you this mod.

{wad_note}

Settings, savegames and the log: Documents\\NarewStudio\\NarewDoom\\
Another IWAD put into this folder (doom.wad, doom2.wad, plutonia.wad, tnt.wad, freedoom1/2.wad) is used instead.

------------------------------------------------------------------------------------------------------------
{gpl}
'''

SHAREWARE_NOTE = ('doom1.wad is the DOOM shareware episode (v1.9, (C) 1993-1994 id Software). id Software allows '
                  'the shareware version to be copied and given to others free of charge, unmodified; it may not '
                  'be sold.')


def dll_exports(data: bytes) -> dict[str, int]:
    return codepatch.PEImage(data).exports()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game')
    ap.add_argument('project')
    ap.add_argument('--wad', required=True, help='IWAD for <game>\\NarewDoom\\ (doom1.wad = shareware)')
    ap.add_argument('--dll', default=os.path.join(ROOT, 'native', 'narewdoom', 'build', DLL_NAME))
    ap.add_argument('--x', type=int, default=40, help='picture position from the left edge (1920x1080 layout)')
    ap.add_argument('--y', type=int, default=-20, help='picture position from the vertical centre')
    ap.add_argument('--name', default='narew_doom')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    def untouched(fname, shas):
        for p in (os.path.join(a.game, fname + '.orig'), os.path.join(a.game, fname)):
            if os.path.isfile(p):
                with open(p, 'rb') as f:
                    data = f.read()
                if codepatch.sha256(data) in shas:
                    return data
        sys.exit(f'{fname}: 아는 빌드({", ".join(shas.values())})의 원본이 아닙니다 (sha256 불일치).')

    n2 = untouched(N2_FILE, N2_SHAS)
    print(f'{N2_FILE}: {N2_SHAS[codepatch.sha256(n2)]} 빌드')
    target = build_n2(n2)
    pf = codepatch.PatchFile(a.name, 'DOOM: 캐릭터 선택 화면 왼쪽 그림에서 DOOM 실행, 클릭하면 키보드가 DOOM으로 '
                             '(NarewEngine2: IGUICore::Render -> NarewDoom.dll, IKeyboard 키 막기)', [target])
    codepatch.apply(n2, target)                 # every check (bytes, relocations, overlaps) once more
    text = codepatch.dumps(pf)
    if a.dry_run:
        print(text)
        return

    with open(a.dll, 'rb') as f:
        dll = f.read()
    ex = dll_exports(dll)
    if PROC_NAME not in ex or any(n.startswith('NarewDoom_Test') for n in ex):
        sys.exit(f'{a.dll}: 배포용 NarewDoom.dll이 아닙니다 (native/narewdoom/build.py 로 빌드하세요)')
    with open(a.wad, 'rb') as f:
        wad = f.read()
    if wad[:4] != b'IWAD':
        sys.exit(f'{a.wad}: IWAD 파일이 아닙니다')
    wad_name = os.path.basename(a.wad).lower()

    from studio.game import GameInstall
    game = GameInstall(a.game)
    mod = ModFolder(a.project)
    name = 'Gui\\character_select.xml'
    raw = game.read(name)
    mod.put(name, add_node(raw.decode('cp949'), a.x, a.y).encode('cp949'))
    mod.put('gui_skin\\' + TEXTURE, placeholder_png())
    mod.write_merge('Language\\word.xml', WORDS)
    mod.put(DLL_NAME, dll)
    mod.put('NarewDoom\\' + wad_name, wad)
    nd = os.path.join(ROOT, 'native', 'narewdoom')
    with open(os.path.join(nd, 'LICENSE'), encoding='utf-8') as f:
        gpl = f.read()
    with open(os.path.join(nd, 'doomgeneric', 'UPSTREAM_COMMIT.txt'), encoding='utf-8') as f:
        commit = f.read().strip()
    note = SHAREWARE_NOTE if wad_name == 'doom1.wad' else f'{wad_name}: the IWAD given when this mod was built.'
    mod.put('NarewDoom\\COPYING.txt', COPYING.format(commit=commit, wad_note=note, gpl=gpl)
            .replace('\n', '\r\n').encode('utf-8'))
    mod.write_patch(a.name, text)
    print(f'{len(target.patches)}개 패치 -> {os.path.join(a.project, "patches", a.name + ".json")}')


if __name__ == '__main__':
    main()
