"""NarewStudio must not block the mod manager: with a GameInstall open on a (fake) game, a pack can
be backed up and replaced, and the studio then reads the original from the .orig backup.

    python tests/test_pack_lock.py <game dir> <scratch dir>
"""
import os
import shutil
import sys

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from narew.pack import PackReader, PackWriter, write_atomic  # noqa: E402
from studio.game import GameInstall  # noqa: E402

fake = os.path.join(SCRATCH, 'game')
os.makedirs(os.path.join(fake, 'data'))
open(os.path.join(fake, 'IsletClient_release(x64).exe'), 'wb').close()
live = os.path.join(fake, 'data', 'xml.pack')
shutil.copy2(os.path.join(GAME, 'data', 'xml.pack'), live)
checks = []

g = GameInstall(fake)
name = 'XML\\product.xml'
before = g.read_located(g.locate(name))

# what the mod manager does: back up once, then rebuild the live pack with a changed entry
shutil.copy2(live, live + '.orig')
w = PackWriter()
with PackReader(live + '.orig') as r:
    for e in r:
        w.copy_from(r, e)
    w.add(r.find(name).name, before + b'\n<!-- modded -->\n')
try:
    write_atomic(live, w)
    replaced = True
except OSError as ex:
    replaced = False
    print('replace failed:', ex)
checks.append(('pack replaced while the studio has the game open', replaced))
checks.append(('no temp file left', not os.path.exists(live + '.tmp')))
after = g.read_located(g.locate(name))
checks.append(('studio still reads the original (from .orig)', after == before))
checks.append(('studio switched to the .orig source', g.sources['xml'].endswith('.orig')))

# a pack replaced without a backup (e.g. a game update): re-indexed, no garbage
g2 = GameInstall(os.path.join(SCRATCH, 'game'))
os.remove(live + '.orig')
w2 = PackWriter()
with PackReader(live) as r:
    for e in r:
        w2.copy_from(r, e)
    w2.add('XML\\zz_new_first.xml', b'<x/>' * 1000)     # shifts nothing, but changes size/mtime
    w2.add(name, b'<products/>')
write_atomic(live, w2)
checks.append(('re-indexed after an update', g2.read_located(g2.locate(name)) == b'<products/>'))
g.close()
g2.close()
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if all(ok for _, ok in checks) else 'SOME CHECKS FAILED')
sys.exit(0 if all(ok for _, ok in checks) else 1)
