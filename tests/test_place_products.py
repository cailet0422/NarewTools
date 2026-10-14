"""Furniture placement in the world editor, end to end on a shipped map.

    python tests/test_place_products.py <game dir> <scratch dir>

Opens map\\64_0.bwr in the real WorldView, places/rotates/undoes/deletes products, saves to a
scratch .bwr and re-reads it strictly (every object must decode with the engine's size rules;
a lamp is saved switched on).
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
for pid, rot in ((324, 0), (1, 0), (175, 1), (261, 2), (830, 3)):      # 324 = a lamp
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
check('undo removes object and collision', len(view.added) == 4 and all(view.store.get(*c) == 0 for c in cells_of(830, r830, a830)))
view.redo()
check('redo puts them back', len(view.added) == 5 and all(view.store.get(*c) == 255 for c in cells_of(830, r830, a830)))

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

# "✋ 설치물 편집": pick a placed object, turn it (R) and switch a lamp (T)
sel = []
view.selection_changed.connect(sel.append)
view.set_tool('edit')
check('no selection: turning asks to pick first', not view.rotate_object() and '고르세요' in msgs[-1])
a30 = free_spot(30, 0)                                       # 3x2 table anchored at a corner
view.set_tool('product')
view.product, view.rot = 30, 0
view.place_product(a30)
alamp = free_spot(324, 0)
view.product, view.rot = 324, 0
view.place_product(alamp)
k30 = next(k for k, v in view.added.items() if v[0] == 30)
klamp = next(k for k, v in view.added.items() if v[0] == 324 and v[1:4] == alamp)
view.set_tool('edit')
view._select(k30)
c0, c1, c2 = (set(cells_of(30, r, a30)) for r in range(3))
check('table footprint changes when turned', c0 != c1)
ok = view.rotate_object()
check('turn a new table 90°: collision moves', ok and view._live(k30)[6] == 1
      and all(view.store.get(*c) == 255 for c in c1) and all(view.store.get(*c) == 0 for c in c0 - c1))
check('selection info follows the turn', sel and sel[-1] and sel[-1]['rot'] == 1 and not sel[-1]['switchable'])
view.undo()
check('undo turn', view._live(k30)[6] == 0 and all(view.store.get(*c) == 255 for c in c0)
      and all(view.store.get(*c) == 0 for c in c1 - c0))
view.redo()
blk = sorted(c2 - c1)[0]
view.commit([(*blk, 4)])
check('blocked turn refused', not view.rotate_object() and '막혀' in msgs[-1] and view._live(k30)[6] == 1)
view.undo()                                                  # the blocking brick
check('a table cannot be switched', not view.toggle_object())
view._select(klamp)
check('a new lamp starts on', view.is_on(klamp) and sel[-1]['switchable'] and sel[-1]['on'])
check('T switches it off', view.toggle_object() and not view.is_on(klamp) and sel[-1]['on'] is False)
view.undo()
check('undo switch: on again', view.is_on(klamp))
view.redo()
turned_old = None                                            # a shipped product that has room to turn
for o in [o for o in view.live_objects() if o[0][0] != 'new' and o[1] == brick.OBJ_PRODUCT]:
    view._select(o[0])
    if view.rotate_object():
        turned_old = view._live(o[0])
        break
check('turn a shipped product', turned_old is not None)
lamp_old = next((o for o in view.live_objects()                # (a tree's type is not a product id)
                 if o[0][0] != 'new' and o[1] == brick.OBJ_PRODUCT and view.switchable(o[2])), None)
check('the map has a shipped lamp', lamp_old is not None)
old_state = view.state_of(lamp_old[0])
view._select(lamp_old[0])
view.toggle_object()
view.set_tool('view')
check('leaving the tool drops the selection', view.sel is None and sel[-1] is None)

# save as a new file and read it back strictly
deleted, added, states, rots = view.object_edits()
check('edits to save: 1 deleted, 7 added, 1 switched, 1 turned', len(deleted) == 1 and len(added) == 7
      and list(states) == [lamp_old[0]] and list(rots) == [turned_old[0]])
regions = {k: view.store.regions[k] for k in view.store.modified}
out = os.path.join(SCRATCH, '64_0_furnished.bwr')
svc.save_as(doc, out, regions, deleted, None, added, states, rots)
view.mark_saved()
w = brick.World.read(out, doc.products, strict=True)
objs = {(o.x, o.y, o.z): o for _, o in w.iter_objects()}
check('strict re-read: object count', len(objs) == len({(o[5], o[6], o[7]) for o in before}) - 1 + 7)
o30, olamp = objs.get(a30), objs.get(alamp)
check('turned table saved with rot 1 and its collision', o30 and o30.rot == 1 and all(w.get(*c) == 255 for c in c1)
      and all(w.get(*c) == 0 for c in c0 - c1))
check('lamp switched off saved without 0x10', olamp and not olamp.state & brick.STATE_ON)
ot = objs.get(tuple(turned_old[3:6]))
check('turned shipped product saved with its new rotation', ot and ot.type_id == turned_old[2] and ot.rot == turned_old[6])
ol = objs.get(tuple(lamp_old[3:6]))
check('switched shipped lamp saved (only bit 0x10 changed)', ol and ol.state == old_state ^ brick.STATE_ON)
for pid, (a, rot) in placed.items():
    o = objs.get(a)
    pt = doc.products.get(pid)
    good = o is not None and o.type_id == pid and o.rot == rot and o.hp == pt.hp and o.extra == brick.new_product_extra(pt)
    check(f'#{pid} saved with rot {rot}, hp and extra', good)
    check(f'#{pid} saved with state {brick.placed_state(pt)}', o is not None and o.state == brick.placed_state(pt))
    check(f'#{pid} collision saved', all(w.get(*c) == 255 for c in cells_of(pid, rot, a)))
check('the lamp is saved on (state bit 0x10)', brick.switchable(doc.products.get(324))
      and objs.get(placed[324][0]) and objs[placed[324][0]].state & brick.STATE_ON)
check('deleted shipped product gone', (old[3], old[4], old[5]) not in objs)
check('view refreshed after save', not view.added and not view.deleted
      and len(view.objects) == len(objs) and not view.undo_stack)

view.close()
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if all(ok for _, ok in checks) else 'SOME CHECKS FAILED')
sys.exit(0 if all(ok for _, ok in checks) else 1)
