"""World saves + capture window, on a FAKE game folder.

    python tests/test_savedworlds.py <game dir (tables)> <a .bwr world save> <scratch dir>

<scratch>/fakegame/Local/<world>.bwr and backup_map/ are made from the given save; the capture
folder is redirected into the scratch dir.  The real game folder is only read for tables.
"""
import ctypes
import os
import shutil
import sys
import threading
import time
from ctypes import wintypes

GAME, SAVE, SCRATCH = sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
os.environ['USERPROFILE'] = os.path.join(SCRATCH, 'home')    # Documents\NarewStudio: shared settings, mods
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from narew import brick  # noqa: E402
from studio import savedworlds, tables  # noqa: E402
from studio.core import Studio, StudioError  # noqa: E402
from studio.svc_world import WorldService  # noqa: E402

fake = os.path.join(SCRATCH, 'fakegame')
dest = os.path.join(SCRATCH, 'captures')
savedworlds.default_capture_dir = lambda: dest
os.makedirs(os.path.join(fake, 'Local'))
os.makedirs(os.path.join(fake, 'backup_map'))
open(os.path.join(fake, 'IsletClient_release(x64).exe'), 'wb').close()      # marker only
live = os.path.join(fake, 'Local', 'lj.bwr')
shutil.copy2(SAVE, live)
for ext, blob in (('.lnd', b'LND' * 100), ('.npc', b'NPC')):
    with open(live[:-4] + ext, 'wb') as f:
        f.write(blob)
shutil.copy2(SAVE, os.path.join(fake, 'backup_map', 'map_10_13_22.bwr'))
os.utime(os.path.join(fake, 'backup_map', 'map_10_13_22.bwr'), (time.time() - 3600,) * 2)
with open(SAVE, 'rb') as f:
    original = f.read()
checks = []

rows = savedworlds.list_saved(fake)
checks.append(('list: current world first, then backups', [r['folder'] for r in rows] == ['Local', 'backup_map']))
checks.append(('is_game_save', savedworlds.is_game_save(fake, live) and not savedworlds.is_game_save(fake, SAVE)
                and savedworlds.is_game_save(None, live)                       # any install, by its exe
                and not savedworlds.is_game_save(None, os.path.join(dest, 'x.bwr'))))

r1 = savedworlds.capture(fake, dest)
with open(r1['path'], 'rb') as f:
    checks.append(('capture copies the save', f.read() == original and r1['new']))
checks.append(('named world_date_time', os.path.basename(r1['path']).startswith('lj_2') and r1['path'].endswith('.bwr')))
checks.append(('.lnd/.npc copied alongside', sorted(os.path.splitext(p)[1] for p in r1['companions']) == ['.lnd', '.npc']
               and open(r1['path'][:-4] + '.npc', 'rb').read() == b'NPC'))
r2 = savedworlds.capture(fake, dest)
checks.append(('same save twice -> no duplicate', r2['path'] == r1['path'] and not r2['new']))

# the game writes a new save while we read: half a file first, the rest a moment later
w = brick.World.from_bytes(original)
w.set(5, 60, 5, 26 if w.get(5, 60, 5) != 26 else 25)
changed = w.to_bytes()


def slow_writer():
    with open(live, 'wb') as f:
        f.write(changed[:len(changed) // 2])
        f.flush()
        time.sleep(1.2)
        f.write(changed[len(changed) // 2:])


time.sleep(1.1)                               # a later mtime than the first capture
t = threading.Thread(target=slow_writer)
t.start()
time.sleep(0.2)
r3 = savedworlds.capture(fake, dest)
t.join()
with open(r3['path'], 'rb') as f:
    checks.append(('capture waits for a complete save', f.read() == changed and r3['new'] and r3['path'] != r1['path']))

# open the save in the world editor backend, edit, save as a new file
st = Studio()
st.open_game(GAME)
svc = WorldService(st)
doc = svc.open_file(live)
checks.append(('open_file', doc.is_world and doc.path == os.path.abspath(live)))
arr = doc.region_array(0, 0)
arr[70, 3, 3] = 26
try:
    svc.save_as(doc, os.path.join(fake, 'Local', 'evil.bwr'), {(0, 0): arr})
    refused = False
except StudioError:
    refused = True
checks.append(('save_as refuses the game save folder', refused and not os.path.exists(os.path.join(fake, 'Local', 'evil.bwr'))))
out = os.path.join(dest, 'lj_edit.bwr')
svc.save_as(doc, out, {(0, 0): arr})
checks.append(('edited copy saved', brick.World.read(out).get(3, 70, 3) == 26))
with open(live, 'rb') as f:
    checks.append(('live save untouched', f.read() == changed))
checks.append(('document now is the new file', doc.path == out))
checks.append(('captured list', any(r['path'] == out for r in savedworlds.list_captured(dest))))

# "내 월드에 적용": replace the game's save with an edited file, with a backup, and restore it
savedworlds.backup_root = lambda: os.path.join(SCRATCH, 'backups')
with open(live, 'rb') as f:
    before_apply = f.read()
companions_before = {ext: open(live[:-4] + ext, 'rb').read() for ext in ('.lnd', '.npc')}
os.utime(out, (time.time() + 5,) * 2)                      # the edited copy is newer than the save
chk = savedworlds.check_apply(fake, out)
checks.append(('check_apply: target + not stale', chk['world'] == 'lj.bwr' and not chk['save_newer']))
os.utime(live, (time.time() + 60,) * 2)
checks.append(('check_apply warns when the game saved later', savedworlds.check_apply(fake, out)['save_newer']))
os.utime(live, (time.time() - 60,) * 2)
res = savedworlds.apply_to_game(fake, out)
with open(live, 'rb') as f, open(out, 'rb') as g:
    checks.append(('apply: save replaced by the edited world', f.read() == g.read()))
bk = res['backup']
checks.append(('apply: .bwr/.lnd/.npc backed up first',
               open(os.path.join(bk, 'lj.bwr'), 'rb').read() == before_apply
               and all(open(os.path.join(bk, 'lj' + e), 'rb').read() == v for e, v in companions_before.items())))
checks.append(('apply: companions untouched', all(open(live[:-4] + e, 'rb').read() == v for e, v in companions_before.items())))
checks.append(('backup listed', [b['dir'] for b in savedworlds.list_backups('lj')] == [bk]))
rr = savedworlds.restore_backup(fake, bk)
with open(live, 'rb') as f:
    checks.append(('restore: original save back', f.read() == before_apply and 'lj.bwr' in rr['restored']))
checks.append(('restore: applied state backed up too', rr['backup_of_current'] and len(savedworlds.list_backups('lj')) == 2))
small = os.path.join(os.path.dirname(GAME), 'extracted', 'map', 'map', '64_flat.bwr')
if os.path.isfile(small):
    try:
        savedworlds.check_apply(fake, small)
        refused = False
    except StudioError:
        refused = True
    checks.append(('different world size refused', refused))
checks.append(('no temp files left in Local', not [n for n in os.listdir(os.path.join(fake, 'Local')) if 'tmp' in n]))

# a save made while a mod was applied: the live pack knows product 942 / brick 149, the .orig does not
from narew import textcodec, xmlmerge  # noqa: E402
from narew.pack import PackReader, PackWriter, write_atomic  # noqa: E402

modgame = os.path.join(SCRATCH, 'modgame')
os.makedirs(os.path.join(modgame, 'data'))
open(os.path.join(modgame, 'IsletClient_release(x64).exe'), 'wb').close()
for p in ('xml', 'default'):
    shutil.copy2(os.path.join(GAME, 'data', p + '.pack'), os.path.join(modgame, 'data', p + '.pack.orig'))
pw = PackWriter()
with PackReader(os.path.join(modgame, 'data', 'xml.pack.orig')) as r:
    for e in r:
        pw.copy_from(r, e)
    for table, tag, src, new in (('XML\\product.xml', 'product', '1', '942'), ('XML\\brick_table.xml', 'brick', '59', '149')):
        base = r.read(r.find(table))
        el = xmlmerge.find(textcodec.decode(base)[0], table, tag, (src,))
        el.set('id', new)
        pw.add(r.find(table).name, xmlmerge.apply_bytes(base, [xmlmerge.upsert(None, table, el)], table))
write_atomic(os.path.join(modgame, 'data', 'xml.pack'), pw)
shutil.copy2(os.path.join(modgame, 'data', 'default.pack.orig'), os.path.join(modgame, 'data', 'default.pack'))
st2 = Studio()
st2.open_game(modgame)
svc2 = WorldService(st2)
plain = brick.World.from_bytes(original, svc2.product_table())
chair_regions = [(r.rx, r.rz) for r in plain.regions if r.objects is None]
checks.append(('(bug reproduced: original table cannot decode the chair region)', chair_regions == [(32, 32)]))
doc2 = svc2.open_file(SAVE)
objs = {o[4] for o in doc2.objects()}
checks.append(('save decodes mod furniture #942', 942 in objs and not any(r.objects is None for r in doc2.obj.regions)))
checks.append(('save knows mod block #149', any(b['id'] == 149 for b in svc2.save_bricks())
               and not any(b['id'] == 149 for b in tables.bricks(st2.read(tables.BRICK_TABLE)))))
checks.append(('save atlas from live pack', svc2.save_atlas().size[0] > 0))
st2.game.close()

# capture window: global shortcut message -> capture
app = QApplication([])
from studio.ui.common import apply_theme  # noqa: E402
from studio.ui.worldcapture import HOTKEY_ID, WM_HOTKEY, CaptureWindow  # noqa: E402

apply_theme(app)
got = []
cap = CaptureWindow(lambda: fake, dest)
cap.captured.connect(got.append)
cap.show()
app.processEvents()
checks.append(('shortcut registered', cap.hotkey))
w.set(6, 60, 6, 26 if w.get(6, 60, 6) != 26 else 25)
time.sleep(1.1)
with open(live, 'wb') as f:
    f.write(w.to_bytes())
ctypes.windll.user32.PostMessageW(wintypes.HWND(int(cap.winId())), WM_HOTKEY, HOTKEY_ID, 0)
end = time.time() + 10
while not got and time.time() < end:
    app.processEvents()
    time.sleep(0.02)
while QThreadPool.globalInstance().activeThreadCount():
    app.processEvents()
checks.append(('shortcut -> capture', bool(got) and got[0]['new'] and os.path.isfile(got[0]['path'])))
cap.grab().save(os.path.join(SCRATCH, 'capture_window.png'))
cap.close()
checks.append(('shortcut released', not cap.hotkey))

for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if all(ok for _, ok in checks) else 'SOME CHECKS FAILED')
sys.exit(0 if all(ok for _, ok in checks) else 1)
