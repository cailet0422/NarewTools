"""Build NarewDoom.dll (64-bit, static CRT) with MSVC.

    python native/narewdoom/build.py [--out DIR] [--test]

Needs Visual Studio (or Build Tools) with the x64 C++ tools; vswhere finds it.  The result is
native/narewdoom/build/NarewDoom.dll unless --out says otherwise.  tools/mod_doom.py takes the DLL from there.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VSWHERE = os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe')
DEFINES = ['FEATURE_SOUND', 'DOOMGENERIC_RESX=640', 'DOOMGENERIC_RESY=400', 'NDEBUG', '_CRT_SECURE_NO_WARNINGS',
           '_CRT_NONSTDC_NO_DEPRECATE']
LIBS = ['user32.lib', 'winmm.lib', 'imm32.lib', 'shell32.lib']


def vcvars() -> str:
    out = subprocess.run([VSWHERE, '-all', '-products', '*', '-requires',
                          'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                         capture_output=True, text=True).stdout.splitlines()
    for root in (r.strip() for r in out):
        bat = os.path.join(root, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat')
        if os.path.isfile(bat):
            return bat
    sys.exit('x64 C++ 빌드 도구(MSVC)를 찾지 못했습니다. Visual Studio Build Tools를 설치하세요.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    ap.add_argument('--test', action='store_true', help='test build (tests/test_narewdoom.py): adds test exports')
    a = ap.parse_args()
    a.out = a.out or os.path.join(HERE, 'build', 'test' if a.test else '')
    defines = DEFINES + (['NAREWDOOM_TEST'] if a.test else [])
    obj = os.path.join(a.out, 'obj')
    os.makedirs(obj, exist_ok=True)
    dg = sorted(glob.glob(os.path.join(HERE, 'doomgeneric', '*.c')))
    ours = [os.path.join(HERE, 'narewdoom.c'), os.path.join(HERE, 'narewdoom_sound.c')]
    common = ['/nologo', '/c', '/O2', '/MT', '/W3', '/GS', '/Zi', '/utf-8', f'/Fo{obj}\\', f'/Fd{obj}\\vc.pdb',
              f'/I{HERE}', f'/I{os.path.join(HERE, "doomgeneric")}'] + [f'/D{d}' for d in defines]
    rsp = os.path.join(obj, 'cl_dg.rsp')
    with open(rsp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(common + ['/FInarew_shim.h', '/wd4244', '/wd4267', '/wd4018', '/wd4996', '/wd4133',
                                    '/wd4146', '/wd4305', '/wd4715'] + [f'"{p}"' for p in dg]))
    rsp2 = os.path.join(obj, 'cl_own.rsp')
    with open(rsp2, 'w', encoding='utf-8') as f:
        f.write('\n'.join([c for c in common if c != '/W3'] + ['/W4', '/wd4100'] + [f'"{p}"' for p in ours]))
    dll = os.path.join(a.out, 'NarewDoom.dll')
    rsp3 = os.path.join(obj, 'link.rsp')
    objs = [os.path.join(obj, os.path.splitext(os.path.basename(p))[0] + '.obj') for p in dg + ours]
    with open(rsp3, 'w', encoding='utf-8') as f:
        f.write('\n'.join(['/nologo', '/DLL', '/DEBUG', '/OPT:REF', '/OPT:ICF', '/DYNAMICBASE', '/NXCOMPAT',
                           f'/OUT:{dll}', f'/PDB:{os.path.join(a.out, "NarewDoom.pdb")}']
                          + [f'"{o}"' for o in objs] + LIBS))
    bat = os.path.join(obj, 'build.bat')
    with open(bat, 'w', encoding='mbcs') as f:
        f.write(f'@echo off\r\ncall "{vcvars()}" >nul || exit /b 1\r\ncl @"{rsp}" || exit /b 1\r\n'
                f'cl @"{rsp2}" || exit /b 1\r\nlink @"{rsp3}" || exit /b 1\r\n')
    r = subprocess.run(['cmd', '/d', '/c', bat], cwd=obj, capture_output=True, text=True,
                       encoding='mbcs', errors='replace')
    lines = [ln for ln in (r.stdout + r.stderr).splitlines()
             if ln.strip() and not ln.strip().endswith('.c')]     # cl prints every file name
    print('\n'.join(lines[-80:]))
    if r.returncode != 0 or not os.path.isfile(dll):
        sys.exit(f'빌드 실패 (exit {r.returncode})')
    print(f'OK {dll} ({os.path.getsize(dll):,} bytes)')


if __name__ == '__main__':
    main()
