"""Engine code patches (narew.codepatch + mod manager) on a FAKE game folder (the real install is only read).

    python tests/test_codepatch.py <real game dir> <scratch dir>

The real IsletEngine_release(x64).dll is copied into <scratch>/game together with one small pack; the
view distance slider patch (tools/mod_view_distance.py) and a few broken/conflicting patch files are
installed as mods, applied, checked, and everything is restored and compared byte for byte.
"""
import hashlib
import os
import shutil
import sys

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
os.environ['USERPROFILE'] = os.path.join(SCRATCH, 'home')    # Documents\NarewStudio: shared settings, mods
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from modmanager.core import Manager  # noqa: E402
from narew import codepatch  # noqa: E402
from narew.modfmt import ModError, ModFolder, ModInfo, extract_zip  # noqa: E402
import mod_view_distance as vd  # noqa: E402

DLL = 'IsletEngine_release(x64).dll'
Manager.game_running = staticmethod(lambda: False)     # only the fake folder is touched; the real game may run
checks = []


def check(name, ok):
    checks.append((name, bool(ok)))


def raises(fn, *a):
    try:
        fn(*a)
    except (codepatch.CodePatchError, ModError) as ex:
        return str(ex)
    return None


fake = os.path.join(SCRATCH, 'game')
os.makedirs(os.path.join(fake, 'data'))
shutil.copy2(os.path.join(GAME, 'data', 'sky.pack.orig' if os.path.isfile(os.path.join(GAME, 'data', 'sky.pack.orig'))
                          else 'sky.pack'), os.path.join(fake, 'data', 'sky.pack'))
src = os.path.join(GAME, DLL + '.orig') if os.path.isfile(os.path.join(GAME, DLL + '.orig')) else os.path.join(GAME, DLL)
shutil.copy2(src, os.path.join(fake, DLL))
open(os.path.join(fake, 'IsletClient_release(x64).exe'), 'wb').close()      # marker only
with open(os.path.join(fake, DLL), 'rb') as f:
    pristine = f.read()
check('test DLL is the build the slider patch is made for', codepatch.sha256(pristine) == vd.SHA256)

# ---- the patch itself
target = vd.build(pristine, 20.0, 225.0, 83, 4100)
patched = codepatch.apply(pristine, target)
img = codepatch.PEImage(patched)
check('20 patches, all inside the file', len(target.patches) == 20)
check('.text virtual size raised to its file size', img.sections[0][2] == vd.TEXT_RAWSIZE)
check('UpdateViewDistance found by its export name',
      img.exports()['?UpdateViewDistance@COption@Islet@@QEAAXXZ'] == vd.UVD - vd.BASE)
check('patch round-trips through JSON', codepatch.apply(pristine, codepatch.parse(codepatch.dumps(
    codepatch.PatchFile('x', '', [target])), 'x').targets[0]) == patched)

# ---- refusals
reloc_rva = img.relocated()[100][0]
o = img.rva_to_offset(reloc_rva, 4)
bad = codepatch.Target(DLL, [codepatch.Patch(pristine[o:o + 4], b'\0' * 4, rva=reloc_rva)])
check('patch over a relocated address is refused', '재배치' in (raises(codepatch.apply, pristine, bad) or ''))
bad = codepatch.Target(DLL, [codepatch.Patch(b'\x90\x90', b'\xcc\xcc', rva=vd.UVD - vd.BASE)])
check('wrong original bytes are refused', '원본 바이트' in (raises(codepatch.apply, pristine, bad) or ''))
bad = codepatch.Target(DLL, target.patches, sha256='0' * 64)
check('other DLL version is refused', '버전' in (raises(codepatch.apply, pristine, bad) or ''))
check('client exe cannot be a target', '패치할 수 없는 파일' in (raises(codepatch.parse, '{"targets": [{"file": '
      '"IsletClient_release(x64).exe", "patches": [{"rva": "0x1000", "original": "00", "patched": "01"}]}]}') or ''))
check('steam_api cannot be a target', raises(codepatch.parse, '{"targets": [{"file": "steam_api64.dll", '
      '"patches": [{"rva": "0x1000", "original": "00", "patched": "01"}]}]}') is not None)
check('lengths must match', raises(codepatch.parse, '{"targets": [{"file": "%s", "patches": [{"rva": "0x1000", '
      '"original": "0000", "patched": "01"}]}]}' % DLL) is not None)
check('unknown symbol is refused', '심볼' in (raises(codepatch.apply, pristine, codepatch.Target(
    DLL, [codepatch.Patch(b'\0', b'\1', symbol='?Nope@@YAXXZ')])) or ''))

# ---- mods: the slider, one that clashes with it, one for another game version
proj = ModFolder.create(os.path.join(SCRATCH, 'slider'), ModInfo(id='slider', name='슬라이더'))
proj.write_patch('view_distance_slider', codepatch.dumps(codepatch.PatchFile('view_distance_slider', 'test', [target])))
check('a mod with code patches is saved as format 3', proj.read_info().format == 3)
check('bad patch text is not saved', raises(proj.write_patch, 'broken', '{"targets": []}') is not None)
clash = ModFolder.create(os.path.join(SCRATCH, 'clash'), ModInfo(id='clash', name='겹침'))
uvd_o = img.rva_to_offset(vd.UVD - vd.BASE, 6)
clash.write_patch('first_bytes', codepatch.dumps(codepatch.PatchFile('first_bytes', '', [codepatch.Target(
    DLL, [codepatch.Patch(pristine[uvd_o:uvd_o + 6], b'\x90' * 5 + b'\xc3', rva=vd.UVD - vd.BASE)])])))
other = ModFolder.create(os.path.join(SCRATCH, 'other'), ModInfo(id='other-version', name='다른 버전'))
other.write_patch('old', codepatch.dumps(codepatch.PatchFile('old', '', [codepatch.Target(
    DLL, target.patches[:2], sha256='1' * 64)])))

zip_path = os.path.join(SCRATCH, 'slider.zip')
proj.export_zip(zip_path)
unz = extract_zip(zip_path, os.path.join(SCRATCH, 'unzipped'))
check('zip keeps patches/', [n for n, _ in unz.iter_patches()] == ['view_distance_slider'])

mgr = Manager()
mgr.set_game(fake)
for m in (clash, other, proj):            # installed last = highest priority
    mgr.install_folder(m.root)
check('install copies patches/', [n for n, _ in ModFolder(os.path.join(mgr.mods_dir, 'slider')).iter_patches()]
      == ['view_distance_slider'])

live = os.path.join(fake, DLL)
r = mgr.apply()
print('report', r)
with open(live, 'rb') as f:
    check('DLL patched exactly as planned', f.read() == patched)
check('untouched copy kept as .orig', open(live + '.orig', 'rb').read() == pristine)
check('report names the applied patch', r['binaries'] == {DLL: ['slider/view_distance_slider']})
check('lower mod touching the same code is skipped', any('clash/first_bytes' in s for s in r['skipped']))
check('patch for another version is skipped', any('other-version/old' in s and '버전' in s for s in r['skipped']))
st = mgr.status()
check('status lists the patched DLL', st['applied'] and st['in_sync'] and st['binaries'] == r['binaries'])

mgr.apply()
with open(live, 'rb') as f:
    check('re-apply starts from .orig (same bytes)', f.read() == patched)

# higher priority flips: the clash wins, the slider is skipped
mgr.set_order([{'id': 'clash', 'enabled': True}, {'id': 'slider', 'enabled': True},
               {'id': 'other-version', 'enabled': False}])
r = mgr.apply()
with open(live, 'rb') as f:
    data = f.read()
check('priority decides which patch goes in', r['binaries'] == {DLL: ['clash/first_bytes']}
      and data[uvd_o:uvd_o + 6] == b'\x90' * 5 + b'\xc3' and any('slider/' in s for s in r['skipped']))

# no patch left -> DLL restored from the backup
mgr.set_order([{'id': 'clash', 'enabled': False}, {'id': 'slider', 'enabled': False}])
r = mgr.apply()
with open(live, 'rb') as f:
    check('disabling every patch restores the DLL', f.read() == pristine and not os.path.exists(live + '.orig')
          and DLL in r['restored'])

# a game update replaces the patched DLL -> that becomes the original, backup dropped
mgr.set_order([{'id': 'slider', 'enabled': True}])
mgr.apply()
updated = bytearray(pristine)
updated[-1] ^= 0xff
with open(live, 'wb') as f:
    f.write(updated)
chk = mgr.check()
check('Steam update detected', DLL in chk['updated'] and not os.path.exists(live + '.orig') and mgr.status()['stale'])
r = mgr.apply()
check('patch is refused on the updated DLL', not r['binaries'] and any('버전' in s for s in r['skipped']))
with open(live, 'rb') as f:
    check('updated DLL left alone', f.read() == bytes(updated))
with open(live, 'wb') as f:
    f.write(pristine)

mgr.apply()
res = mgr.restore_all()
with open(live, 'rb') as f:
    check('restore gives the original DLL back', f.read() == pristine and not os.path.exists(live + '.orig')
          and DLL in res['restored'] and not mgr.status()['applied'])

# ---- free camera: one patch file over two DLLs goes in whole or not at all, next to the slider
import mod_freecam as fc  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

N2 = fc.N2_FILE
n2src = os.path.join(GAME, N2 + '.orig') if os.path.isfile(os.path.join(GAME, N2 + '.orig')) else os.path.join(GAME, N2)
shutil.copy2(n2src, os.path.join(fake, N2))
with open(os.path.join(fake, N2), 'rb') as f:
    n2_pristine = f.read()
check('test NarewEngine2 is the build the free camera patch is made for', codepatch.sha256(n2_pristine) == fc.N2_SHA)
t_n2, t_ie = fc.build_n2(n2_pristine), fc.build_ie(pristine, n2_pristine, 20.0, 4.0)
n2_patched, ie_fc = codepatch.apply(n2_pristine, t_n2), codepatch.apply(pristine, t_ie)
n2img = codepatch.PEImage(n2_patched)
data_sec = [s for s in n2img.sections if s[0] == '.data'][0]
check('NarewEngine2 .data mapped up to the state block', data_sec[1] + data_sec[2] >= fc.N2_STATE - fc.BASE + 0x40)
check('signature at the start of the NarewEngine2 code', n2_patched[n2img.rva_to_offset(fc.N2_CAVE - fc.BASE, 8):][:8]
      == fc.SIG)
fcmod = ModFolder.create(os.path.join(SCRATCH, 'freecam'), ModInfo(id='freecam', name='프리캠'))
fcmod.write_patch('free_camera', codepatch.dumps(codepatch.PatchFile('free_camera', '', [t_n2, t_ie])))
mgr.install_folder(fcmod.root)
mgr.set_order([{'id': 'freecam', 'enabled': True}, {'id': 'slider', 'enabled': True}])
r = mgr.apply()
with open(live, 'rb') as f:
    ie_live = f.read()
with open(os.path.join(fake, N2), 'rb') as f:
    n2_live = f.read()
both = bytearray(ie_fc)                     # slider on top of the free camera bytes = what the manager wrote
for o, p in codepatch.plan(pristine, target):
    both[o:o + len(p.patched)] = p.patched
check('free camera and slider share the engine DLL', ie_live == bytes(both) and r['binaries'].get(DLL) ==
      ['freecam/free_camera', 'slider/view_distance_slider'])
check('free camera patches NarewEngine2 too', n2_live == n2_patched and r['binaries'].get(N2) == ['freecam/free_camera'])
# a NarewEngine2 of another build: nothing of the free camera goes in (the game DLL only gets the slider)
other_n2 = bytearray(n2_pristine)
other_n2[-1] ^= 0xff
mgr.restore_all()
with open(os.path.join(fake, N2), 'wb') as f:
    f.write(other_n2)
r = mgr.apply()
with open(live, 'rb') as f:
    ie_live = f.read()
check('patch file over two DLLs is all or nothing', ie_live == patched and N2 not in r['binaries']
      and any('freecam/free_camera' in s for s in r['skipped']))
with open(os.path.join(fake, N2), 'rb') as f:
    check('other NarewEngine2 left alone', f.read() == bytes(other_n2))
mgr.restore_all()
with open(os.path.join(fake, N2), 'wb') as f:
    f.write(n2_pristine)
# GUI: the two buttons go at the end of the root node and the layout stays valid XML
from studio.game import GameInstall  # noqa: E402
gi = GameInstall(GAME)
for name, x in (('Gui\\game_menu_top.xml', -850), ('Gui\\game_menu.xml', -660)):
    xml = fc.add_buttons(gi.read(name).decode('utf-8-sig'), x)
    root = ET.fromstring(xml.encode('utf-8'))
    if root.tag != 'node':                  # some layouts wrap the root node in <root>
        root = root.find('node')
    kids = [n for n in root if n.tag == 'node']
    check(f'{name}: buttons are the last children of the root',
          [k.get('id') for k in kids[-2:]] == [str(fc.ID_ENTER), str(fc.ID_EXIT)] and kids[-1].get('visible') == '0'
          and kids[-1].find('event[@type="mouse_click"]').get('id') == str(fc.ID_EXIT))
gi.close()

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if not bad else f'FAILED: {bad}')
sys.exit(1 if bad else 0)
