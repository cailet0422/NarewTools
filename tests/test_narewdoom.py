"""NarewDoom.dll without the game: builds the test DLL and tests/test_narewdoom.c, then runs DOOM against a real
D3D9 texture and the real (only mapped) NarewEngine2 DLL.  Sound runs silent (NAREWDOOM_SILENT=1) and the DOOM
config goes to the scratch dir, so nothing of the user's is touched.

    python tests/test_narewdoom.py <game folder> <scratch dir> <doom1.wad (or another IWAD)>

The game folder is only read (the pristine NarewEngine2_Release64.dll, or its .orig when the mod manager patched it).
Snapshots of the texture land in <scratch>/run/*.png.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'native', 'narewdoom'))
import build  # noqa: E402

N2 = 'NarewEngine2_Release64.dll'
N2_SHAS = ('40d2a2fc50b3cc3e235ca2afcdc18cfe1f679c921528fecbc15a2191b6adb23c',     # 2026-10-03
           '3ce895c96dbadc050f4e3d34d8bc85861fcea15a67da32efc70055f5c17ee288')     # 2026-10-16 (same code)


def pristine_n2(game: str) -> str:
    for p in (os.path.join(game, N2 + '.orig'), os.path.join(game, N2)):
        if os.path.isfile(p) and hashlib.sha256(open(p, 'rb').read()).hexdigest() in N2_SHAS:
            return p
    sys.exit('아는 빌드의 NarewEngine2_Release64.dll 원본을 찾지 못했습니다')


def main():
    game, scratch, wad = sys.argv[1], os.path.abspath(sys.argv[2]), sys.argv[3]
    run = os.path.join(scratch, 'run')
    shutil.rmtree(run, ignore_errors=True)
    os.makedirs(os.path.join(run, 'NarewDoom'))
    os.makedirs(os.path.join(run, 'n2'))
    # 1) test build of the DLL
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'native', 'narewdoom', 'build.py'), '--test',
                        '--out', os.path.join(scratch, 'dll')], capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit('NarewDoom 테스트 빌드 실패')
    shutil.copy2(os.path.join(scratch, 'dll', 'NarewDoom.dll'), os.path.join(run, 'NarewDoom.dll'))
    shutil.copy2(wad, os.path.join(run, 'NarewDoom', os.path.basename(wad).lower()))
    shutil.copy2(pristine_n2(game), os.path.join(run, 'n2', N2))
    # 2) the harness
    exe = os.path.join(scratch, 'test_narewdoom.exe')
    src = os.path.join(ROOT, 'tests', 'test_narewdoom.c')
    bat = os.path.join(scratch, 'harness.bat')
    with open(bat, 'w', encoding='mbcs') as f:
        f.write(f'@echo off\r\ncall "{build.vcvars()}" >nul || exit /b 1\r\n'
                f'cl /nologo /O2 /W3 /MT "{src}" /Fe"{exe}" /Fo"{scratch}\\\\" d3d9.lib user32.lib\r\n')
    r = subprocess.run(['cmd', '/d', '/c', bat], cwd=scratch, capture_output=True, text=True, encoding='mbcs',
                       errors='replace')
    if r.returncode or not os.path.isfile(exe):
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit('하네스 빌드 실패')
    # 3) run
    env = dict(os.environ, NAREWDOOM_CONFIGDIR=os.path.join(scratch, 'config'), NAREWDOOM_SILENT='1')
    r = subprocess.run([exe, os.path.join(run, 'n2', N2), os.path.join(run, 'NarewDoom.dll'), run],
                       capture_output=True, text=True, env=env, timeout=120, encoding='mbcs', errors='replace')
    print(r.stdout)
    if r.stderr.strip():
        print(r.stderr[-2000:])
    log = os.path.join(scratch, 'config', 'narewdoom.log')
    if os.path.isfile(log):
        print('--- narewdoom.log'), print(open(log, encoding='mbcs', errors='replace').read()[-3000:])
    try:
        from PIL import Image
        for fn in sorted(os.listdir(run)):
            if fn.endswith('.bmp'):
                Image.open(os.path.join(run, fn)).save(os.path.join(run, fn[:-4] + '.png'))
    except ImportError:
        pass
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith('CHECK') and ' FAIL' in ln]
    ok = r.returncode == 0 and not fails and 'DONE OK' in r.stdout
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    sys.path.insert(0, ROOT)
    import mod_doom
    from narew import codepatch
    # 0) the layout: the client takes IGUIParser::GetFirstNode() of character_select.xml as the character panel and
    #    calls SetVisible on its buttons 60/61/62 (a NULL there crashed the game in 1.0.1, DOOM node first)
    import re
    import xml.etree.ElementTree as ET
    from studio.game import GameInstall
    raw = GameInstall(game).read('Gui\\character_select.xml').decode('cp949')
    tops = [n for n in ET.fromstring(re.sub(r'^<\?xml[^>]*\?>', '', mod_doom.add_node(raw, 40, -20).strip()))
            if n.tag == 'node']
    first_ids = {n.get('id') for n in tops[0].iter('node')}
    lay_ok = tops[0].get('id') == '1' and {'60', '61', '62'} <= first_ids and tops[1].get('id') == str(mod_doom.NODE_ID)
    print(f'CHECK layout_first_node {"OK" if lay_ok else "FAIL"} top-level ids {[n.get("id") for n in tops]}')
    ok = ok and lay_ok
    # 4) the PATCHED NarewEngine2 code (tools/mod_doom.py) loads NarewDoom.dll from the exe's folder by itself
    eng = os.path.join(scratch, 'engine')
    shutil.rmtree(eng, ignore_errors=True)
    os.makedirs(os.path.join(eng, 'NarewDoom'))
    os.makedirs(os.path.join(eng, 'n2'))
    pristine = open(pristine_n2(game), 'rb').read()
    with open(os.path.join(eng, 'n2', N2), 'wb') as f:
        f.write(codepatch.apply(pristine, mod_doom.build_n2(pristine)))
    shutil.copy2(exe, os.path.join(eng, 'test_narewdoom.exe'))
    shutil.copy2(os.path.join(run, 'NarewDoom.dll'), os.path.join(eng, 'NarewDoom.dll'))
    shutil.copy2(wad, os.path.join(eng, 'NarewDoom', os.path.basename(wad).lower()))
    env['NAREWDOOM_CONFIGDIR'] = os.path.join(scratch, 'config-engine')
    r2 = subprocess.run([os.path.join(eng, 'test_narewdoom.exe'), '--engine', os.path.join(eng, 'n2', N2), eng],
                        capture_output=True, text=True, env=env, timeout=120, encoding='mbcs', errors='replace',
                        cwd=scratch)
    print('--- patched engine')
    print('\n'.join(ln for ln in r2.stdout.splitlines() if ln.startswith(('CHECK', 'DONE'))))
    fails2 = [ln for ln in r2.stdout.splitlines() if ln.startswith('CHECK') and ' FAIL' in ln]
    ok = ok and r2.returncode == 0 and not fails2 and 'DONE OK' in r2.stdout
    print('ALL OK' if ok else f'FAILED ({len(fails)} checks, exit {r.returncode})')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
