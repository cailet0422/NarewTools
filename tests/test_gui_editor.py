"""GUI editor: open Gui\\inventory.xml, move the close button by drag-equivalent and arrow keys,
undo/redo, save into a scratch mod project, and check only the <pos> of that node changed.

    python tests/test_gui_editor.py <game dir> <scratch dir>
"""
import os
import sys
import time

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from narew import textcodec  # noqa: E402
from narew.modfmt import ModInfo  # noqa: E402
from studio import guilayout as G  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.ui.common import apply_theme  # noqa: E402
from studio.ui.glview import set_default_gl_format  # noqa: E402
from studio.ui.mainwindow import MainWindow  # noqa: E402

errors = []
sys.excepthook = lambda *exc: errors.append(exc)
QMessageBox.warning = staticmethod(lambda parent, title, msg, *a: errors.append(msg))
checks = []


def check(n, ok):
    checks.append((n, bool(ok)))


def pump(s=0.3):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


set_default_gl_format()
app = QApplication([])
apply_theme(app)
st = Studio()
st.open_game(GAME)
st.new_project(os.path.join(SCRATCH, 'projects'), ModInfo(id='gui-test', name='GUI 테스트'))
win = MainWindow(st)
win.resize(1500, 900)
win.show()
win.open_page('gui')
page = win.page('gui')
pump()
names = page.layouts()
check('layouts listed', len(names) > 150 and any(n.lower().endswith('inventory.xml') for n in names))
inv = next(n for n in names if n.lower().endswith('\\inventory.xml'))
page.open_layout(inv)
pump(1)
original = page.text
nodes = G.all_nodes(page.root)
idx = next(i for i, n in enumerate(nodes) if n.child('texture') is not None
           and n.child('texture').text.endswith('Btn_Close.png'))
b0 = page.boxes[idx]
check('close button laid out top-right', b0.w == 70 and b0.h == 70 and b0.x > 1000)
check('close button drawn with its image', page.pixmap(nodes[idx].child('texture').text) is not None)
drawn = sum(1 for it in page.items if it is not None)
check('nodes drawn', drawn > 20)
win.grab().save(os.path.join(SCRATCH, 'gui_inventory.png'))

page.select(idx)
page.move_node(idx, -120, 40)                 # what a drag of (-120, +40) does
x1, y1 = G.node_pos(G.all_nodes(page.root)[idx])
check('drag moves pos', (x1, y1) == (-13 - 120, 20 + 40))
check('box follows', abs(page.boxes[idx].x - (b0.x - 120)) < 0.01 and abs(page.boxes[idx].y - (b0.y + 40)) < 0.01)
page.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.ShiftModifier))
check('shift+arrow nudges 10px', G.node_pos(G.all_nodes(page.root)[idx]) == (x1 + 10, y1))
page.undo()
check('undo', G.node_pos(G.all_nodes(page.root)[idx]) == (x1, y1))
page.redo()
page.undo()
diff_lines = [(a, b) for a, b in zip(original.splitlines(), page.text.splitlines()) if a != b]
check('only the <pos> line changed', len(diff_lines) == 1 and '<pos' in diff_lines[0][1]
      and len(original.splitlines()) == len(page.text.splitlines()))
# ---- new elements, text, multi-select, delete
from studio.ui import page_gui  # noqa: E402

page_gui.ImagePicker.exec = lambda self: (setattr(self, 'result_path', '00_Common\\Btn_Accpet1.png'), 1)[1]
n_before = len(G.all_nodes(page.root))
page.select(0)
page.add_element('image')
page.add_element('button')
page.add_element('extended_text')
nodes = G.all_nodes(page.root)
added = [i for i, n in enumerate(nodes) if n.attrs.get('desc') == 'NarewStudio']
check('3 elements added inside the window', len(nodes) == n_before + 3 and len(added) == 3
      and all(G.is_ancestor(nodes[0], nodes[i]) for i in added))
img = nodes[added[0]]
check('new image uses the picked texture and its size', img.child('texture').text == '00_Common\\Btn_Accpet1.png'
      and page.boxes[added[0]].w == 46)
ti = added[2]
page.select(ti)
page.txt.setText('나루 <테스트> & UI')
page._text_changed()
page.fsize.setValue(22)
n = G.all_nodes(page.root)[ti]
check('text and font size set', G.node_text(n) == '나루 <테스트> & UI' and G._num(n.child('font').attrs.get('size')) == 22)
page.set_text(G.set_text_color(page.text, n, (255, 200, 0, 255)))
check('text colour set', G.all_nodes(page.root)[ti].child('color').attrs.get('font') == '0xFFFFC800')
# multi-select: the image and the button move together
page.scene.clearSelection()
for i in added[:2]:
    page.items[i].setSelected(True)
p0 = [G.node_pos(G.all_nodes(page.root)[i]) for i in added[:2]]
page.move_nodes(page.selected_indices(), 30, -10)
p1 = [G.node_pos(G.all_nodes(page.root)[i]) for i in added[:2]]
check('two selected elements moved together', all(b == (a[0] + 30, a[1] - 10) for a, b in zip(p0, p1)))
check('selection kept after the move', sorted(page.selected_indices()) == sorted(added[:2]))
# delete the button (no confirm dialog in tests)
page_gui.confirm = lambda *a, **k: True
page.scene.clearSelection()
page.items[added[1]].setSelected(True)
page.delete_selected()
nodes = G.all_nodes(page.root)
check('element deleted', len(nodes) == n_before + 2 and sum(1 for n in nodes if n.attrs.get('desc') == 'NarewStudio') == 2)
pump(0.3)
win.grab().save(os.path.join(SCRATCH, 'gui_added.png'))
page.undo()
check('undo brings it back', len(G.all_nodes(page.root)) == n_before + 3)
page.redo()

page.save()
saved = st.need_project().find(inv)
check('saved into the mod project', saved is not None)
text, info = textcodec.decode(open(saved, 'rb').read())
_, oinfo = textcodec.decode(st.read(inv, 'game'))
check('encoding kept', (info.encoding, info.bom, info.newline) == (oinfo.encoding, oinfo.bom, oinfo.newline))
check('saved text = editor text', text == page.text)
sr = G.parse(text)
check('saved file: new elements + Korean text survive the file encoding',
      sum(1 for n in G.all_nodes(sr) if n.attrs.get('desc') == 'NarewStudio') == 2
      and any(G.node_text(n) == '나루 <테스트> & UI' for n in G.all_nodes(sr)))
pump(0.3)
win.grab().save(os.path.join(SCRATCH, 'gui_inventory_moved.png'))
win.close()
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
for e in errors:
    print('ERROR', e)
ok = all(c for _, c in checks) and not errors
print('ALL OK' if ok else 'SOME CHECKS FAILED')
sys.exit(0 if ok else 1)
