"""Mod manager end-to-end check on a FAKE game folder (the real install is only read).

    python tests/test_modmanager.py <real game dir> <mod project dir> <scratch dir>

A subset of the real packs and the Language folder is copied into <scratch>/game; the mod
project (e.g. the one tests/test_creator.py makes) is installed together with a second small mod,
applied, checked, then everything is restored and compared byte for byte with the originals.
"""
import hashlib
import os
import re
import shutil
import sys

GAME, PROJECT, SCRATCH = sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modmanager.core import Manager  # noqa: E402
from narew import textcodec, xmlmerge  # noqa: E402
from narew.modfmt import ModFolder, ModInfo  # noqa: E402
from narew.pack import PackReader  # noqa: E402

PACKS = ('xml', 'gui', 'sky', 'patch')
fake = os.path.join(SCRATCH, 'game')
os.makedirs(os.path.join(fake, 'data'))
for p in PACKS:
    src = os.path.join(GAME, 'data', p + '.pack')
    if os.path.isfile(src + '.orig'):
        src += '.orig'
    shutil.copy2(src, os.path.join(fake, 'data', p + '.pack'))
shutil.copytree(os.path.join(GAME, 'Language'), os.path.join(fake, 'Language'))
open(os.path.join(fake, 'IsletClient_release(x64).exe'), 'wb').close()      # marker only


def digest_tree(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            full = os.path.join(d, f)
            with open(full, 'rb') as fh:
                out[os.path.relpath(full, root)] = hashlib.md5(fh.read()).hexdigest()
    return out


before = digest_tree(fake)
checks = []

# second mod: renames one item the first mod also adds (higher priority must win) + a new string
proj = ModFolder(PROJECT)
items_patch = proj.read_merge('XML\\item_table.xml')
first_item = re.search(r'<item id="(\d+)">', items_patch).group(1)
m2 = ModFolder.create(os.path.join(SCRATCH, 'mod2'), ModInfo(id='second-mod', name='둘째 모드'))
m2.write_merge('XML\\item_table.xml', re.sub(
    r'(<item id="%s">.*?<name>)[^<]*(</name>)' % first_item, r'\g<1>Renamed By Mod2\g<2>',
    '<merge>' + re.search(r'<item id="%s">.*?</item>' % first_item, items_patch, re.S).group(0) + '</merge>',
    flags=re.S))
m2.write_merge('Language\\string.xml',
               '<merge><string table="item_name" index="19999"><kor>둘째 모드 문자열</kor><eng>Second</eng></string></merge>')

mgr = Manager()
mgr.set_game(fake)
mgr.install_folder(PROJECT)
mgr.install_folder(m2.root)                  # installed last = highest priority
order = [m['id'] for m in mgr.installed()]
print('order', order)
checks.append(('second mod has priority', order[0] == 'second-mod'))
installed = ModFolder(os.path.join(mgr.mods_dir, ModFolder(PROJECT).read_info().id))
checks.append(('merges copied on install', len(list(installed.iter_merges())) == len(list(proj.iter_merges()))))

report = mgr.apply()
print('report', {k: v for k, v in report.items() if k != 'merged'})
for name, r in report['merged'].items():
    print('  merged', name, r)


def pack_text(stem, entry):
    with PackReader(os.path.join(fake, 'data', stem + '.pack')) as r:
        e = r.find(entry)
        return textcodec.decode(r.read(e))[0] if e else None


def find_pack_text(entry):
    for p in PACKS:
        t = pack_text(p, entry)
        if t is not None:
            return t
    return None


item_table = find_pack_text('XML\\item_table.xml')
checks.append(('new items in packed item_table', all(f'<item id="{i}">' in item_table
                                                    for i in re.findall(r'<item id="(\d+)">', items_patch))))
checks.append(('higher priority mod wins a record',
               re.search(r'<item id="%s">\s*<name>Renamed By Mod2</name>' % first_item, item_table) is not None))
checks.append(('item_table still parses', xmlmerge.find(item_table, 'XML\\item_table.xml', 'item', ('1',)) is not None))
checks.append(('brick_table merged', re.search(r'<brick id="149"', find_pack_text('XML\\brick_table.xml')) is not None))
checks.append(('Sky\\sky.xml merged', 'beep.wav' in (find_pack_text('Sky\\sky.xml') or '')))
checks.append(('Gui\\sound.xml merged', 'beep.wav' in (find_pack_text('Gui\\sound.xml') or '').lower()))

lang = os.path.join(fake, 'Language', 'string.xml')
with open(lang, 'rb') as f:
    lang_data = f.read()
with open(os.path.join(GAME, 'Language', 'string.xml'), 'rb') as f:
    lang_orig = f.read()
lt, linfo = textcodec.decode(lang_data)
_, oinfo = textcodec.decode(lang_orig)
checks.append(('string.xml overridden with backup', os.path.isfile(lang + '.orig') and 'Language\\string.xml' in report['overridden']))
checks.append(('string.xml has both mods', '둘째 모드 문자열' in lt and re.search(r'index="%s"' % first_item, lt) is not None))
checks.append(('string.xml keeps encoding/newlines', (linfo.encoding, linfo.bom, linfo.newline) == (oinfo.encoding, oinfo.bom, oinfo.newline)))
checks.append(('string.xml only grew', 0 < len(lang_data) - len(lang_orig) < 20000))
checks.append(('new loose files written', report['loose'] > 1))
checks.append(('status in sync', mgr.status()['in_sync']))

# re-apply is stable (merges always start from the pristine originals)
mgr.apply()
with open(lang, 'rb') as f:
    checks.append(('re-apply is idempotent', f.read() == lang_data))

# game update rewrites string.xml -> backup dropped, restore keeps the game's version
updated = lang_orig + b'\r\n<!-- update -->'
with open(lang, 'wb') as f:
    f.write(updated)
chk = mgr.check()
print('check after fake update', chk)
checks.append(('update detected', any('string.xml' in u for u in chk['updated']) and not os.path.exists(lang + '.orig')))
mgr.apply()
with open(lang, 'rb') as f:
    checks.append(('re-apply merges onto the updated file', b'<!-- update -->' in f.read()))
mgr.restore_all()
with open(lang, 'rb') as f:
    checks.append(('restore gives the updated original back', f.read() == updated))
with open(lang, 'wb') as f:
    f.write(lang_orig)

after = digest_tree(fake)
checks.append(('restore is byte-identical', after == before))
if after != before:
    print('differs:', sorted(set(after.items()) ^ set(before.items()))[:10])

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if not bad else f'FAILED: {bad}')
sys.exit(1 if bad else 0)
