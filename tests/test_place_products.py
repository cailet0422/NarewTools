"""Furniture placement in the world editor, end to end on a shipped map.

    python tests/test_place_products.py <game dir> <scratch dir>

Opens map\\64_0.bwr in the real WorldView, places/rotates/undoes/deletes products, saves to a
scratch .bwr and re-reads it strictly (every object must decode with the engine's size rules).
"""
import os
import sys
import time

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from narew import brick  # noqa: E402
from studio import svc_blocks as B  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.gl.voxels import BrickLUT  # noqa: E402
from studio.svc_world import WorldService  # noqa: E402
from studio.ui.common import apply_theme  # noqa: E402
from studio.ui.glview import set_default_gl_format  # noqa: E402
from studio.ui.worldview import WorldView  # noqa: E402

checks = []


def check(name, ok):
    checks.append((name, bool(ok)))


set_default_gl_format()
app = QApplication([])
apply_theme(app)
st = Studio()
st.open_game(GAME)
svc = WorldService(st)
doc = svc.open('map\\64_0.bwr')
before = doc.objects()
view = WorldView()
view.resize(640, 480)
view.show()
view.open(doc, BrickLUT(B.bricks(st)), B.atlas_image(st))
end = time.time() + 20
while (view.store is None or len(view.store.regions) < doc.rx * doc.rz) and time.time() < end:
    app.processEvents()
    time.sleep(0.02)
check('world loaded in the view', view.store and len(view.store.regions) == doc.rx * doc.rz)
msgs = []
view.message.connect(msgs.append)


def ground(x, z):
    return (x, view.store.top_y(x, z) + 1, z)


def cells_of(pid, rot, a):
    return [(a[0] + dx, a[1] + dy, a[2] + dz) for dx, dy, dz in brick.product_footprint(doc.products.get(pid), rot)]


def free_spot(pid, rot):
    for z in range(4, 60, 3):
        for x in range(4, 60, 3):
            a = ground(x, z)
            view.product, view.rot = pid, rot
            if view._product_check(a)[0]:
                return a
    raise SystemExit(f'no free spot for #{pid}')


placed = {}
for pid, rot in ((1, 0), (175, 1), (261, 2), (830, 3)):
    a = free_spot(pid, rot)
    view.product, view.rot = pid, rot
    ok = view.place_product(a)
    placed[pid] = (a, rot)
    check(f'place #{pid} rot{rot}', ok and all(view.store.get(*c) == 255 for c in cells_of(pid, rot, a)))
a1 = placed[1][0]
view.product, view.rot = 1, 0
check('blocked spot refused', not view.place_product(a1) and '막혀' in msgs[-1])
sign = next(p for p in doc.product_choices() if p['reason'])
view.product = sign['id']
check(f'unsupported #{sign["id"]} ({sign["event"]}) refused', not view.place_product(ground(2, 2)))

# undo / redo the last placement
a830, r830 = placed[830]
view.undo()
check('undo removes object and collision', len(view.added) == 3 and all(view.store.get(*c) == 0 for c in cells_of(830, r830, a830)))
view.redo()
check('redo puts them back', len(view.added) == 4 and all(view.store.get(*c) == 255 for c in cells_of(830, r830, a830)))

# delete a new object, then undo the delete
key1 = next(k for k, v in view.added.items() if v[0] == 1)
view.delete_object(key1)
check('delete new object clears its collision', view.store.get(*a1) == 0)
view.undo()
check('undo delete restores it', view.store.get(*a1) == 255 and key1 not in view.deleted)

# delete an existing shipped product
old = next(o for o in view.live_objects() if o[0][0] != 'new' and o[1] == brick.OBJ_PRODUCT)
old_cells = cells_of(old[2], old[6], (old[3], old[4], old[5]))
view.delete_object(old[0])
check('delete shipped product clears its collision', all(view.store.get(*c) == 0 for c in old_cells))

# save as a new file and read it back strictly
deleted, added = view.object_edits()
check('edits to save: 1 deleted, 4 added', len(deleted) == 1 and len(added) == 4)
regions = {k: view.store.regions[k] for k in view.store.modified}
out = os.path.join(SCRATCH, '64_0_furnished.bwr')
svc.save_as(doc, out, regions, deleted, None, added)
view.mark_saved()
w = brick.World.read(out, doc.products, strict=True)
objs = {(o.x, o.y, o.z): o for _, o in w.iter_objects()}
check('strict re-read: object count', len(objs) == len({(o[5], o[6], o[7]) for o in before}) - 1 + 4)
for pid, (a, rot) in placed.items():
    o = objs.get(a)
    pt = doc.products.get(pid)
    good = o is not None and o.type_id == pid and o.rot == rot and o.hp == pt.hp and o.extra == brick.new_product_extra(pt)
    check(f'#{pid} saved with rot {rot}, hp and extra', good)
    check(f'#{pid} collision saved', all(w.get(*c) == 255 for c in cells_of(pid, rot, a)))
check('deleted shipped product gone', (old[3], old[4], old[5]) not in objs)
check('view refreshed after save', not view.added and not view.deleted
      and len(view.objects) == len(objs) and not view.undo_stack)

view.close()
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if all(ok for _, ok in checks) else 'SOME CHECKS FAILED')
sys.exit(0 if all(ok for _, ok in checks) else 1)
