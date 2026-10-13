"""UI smoke test: open every NarewStudio page against a game folder and save a screenshot of each.

    python tests/smoke_ui.py <game dir> <scratch dir>
APPDATA/LOCALAPPDATA are redirected into the scratch dir; the mod project is created there too.
"""
import os
import sys
import time
import traceback

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from narew.modfmt import ModInfo  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.ui import common  # noqa: E402
from studio.ui.glview import set_default_gl_format  # noqa: E402
from studio.ui.mainwindow import PAGES, MainWindow  # noqa: E402

errors = []
sys.excepthook = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
# modal dialogs would block the run: record them instead
QMessageBox.warning = staticmethod(lambda parent, title, msg, *a: errors.append(f'warning dialog: {msg}'))
QMessageBox.information = staticmethod(lambda parent, title, msg, *a: print('info dialog:', msg))
QMessageBox.exec = lambda self: errors.append(f'modal dialog: {self.text()}')


def pump(seconds=0.5, settle=True):
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end or (settle and QThreadPool.globalInstance().activeThreadCount() and time.time() < end + 60):
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


set_default_gl_format()
app = QApplication([])
common.apply_theme(app)
st = Studio()
st.open_game(GAME)
st.new_project(os.path.join(SCRATCH, 'projects'), ModInfo(id='ui-smoke', name='UI 테스트'))
win = MainWindow(st)
win.resize(1500, 900)
win.show()
pump(1)
shots = os.path.join(SCRATCH, 'shots')
os.makedirs(shots, exist_ok=True)
for key, *_ in PAGES:
    if key == '-':
        continue
    t = time.time()
    try:
        win.open_page(key)
        pump(2.5)
    except Exception:
        errors.append(f'{key}: ' + traceback.format_exc())
    win.grab().save(os.path.join(shots, f'{key}.png'))
    print(f'{key:10s} {time.time() - t:5.1f}s')

# exercise a few interactions
try:
    win.open_page('create')
    page = win.page('create')
    for i in range(page.tabs.count()):
        page.tabs.setCurrentIndex(i)
        pump(1.0)
        win.grab().save(os.path.join(shots, f'create_{i}.png'))
    win.open_page('models')
    page = win.page('models')
    pump(1)
    hit = page.list.findItems('npc_bear', Qt.MatchContains) or page.list.findItems('.nwmt', Qt.MatchContains)
    if hit:
        page.list.setCurrentItem(hit[0])
        page.list.itemClicked.emit(hit[0])
        pump(4)
        win.grab().save(os.path.join(shots, 'models_selected.png'))
    else:
        errors.append('models: list empty')
    page = win.page('furniture')
    win.open_page('furniture')
    pump(1)
    if page.list.count():
        page.list.setCurrentRow(1)
        pump(3)
        win.grab().save(os.path.join(shots, 'furniture_selected.png'))
    page = win.page('world')
    win.open_page('world')
    pump(1)
    hit = page.list.findItems('64_0.bwr', Qt.MatchContains)
    if hit:
        page.list.setCurrentItem(hit[0])
        page.list.itemClicked.emit(hit[0])
        pump(8)
        win.grab().save(os.path.join(shots, 'world_opened.png'))
        # furniture tool: pick a chair in the 설치물 tab and place a few near the camera
        chair = page.prod_list.findItems('#1 ', Qt.MatchStartsWith)
        if chair:
            page.prod_list.itemClicked.emit(chair[0])
            v = page.view
            cx, cz = int(v.cam.target[0]), int(-v.cam.target[2])
            end = time.time() + 30                 # under load the regions may still be streaming in
            while time.time() < end and not all(((cx + dx) >> 4, (cz + dz) >> 4) in v.store.regions
                                                 for dx in (-6, 6) for dz in (-6, 6)):
                pump(0.2, settle=False)
            placed = 0
            for dz in range(-6, 7, 3):
                for dx in range(-6, 7, 3):
                    cell = (cx + dx, v.store.top_y(cx + dx, cz + dz) + 1, cz + dz)
                    if v._product_check(cell)[0] and v.place_product(cell):
                        placed += 1
                        v.rotate_product()
            pump(1)
            win.grab().save(os.path.join(shots, 'world_products.png'))
            if not placed or page.right_tabs.currentIndex() != 1:
                errors.append(f'world: furniture placement (placed {placed})')
            while v.undo_stack:                # leave the world unchanged (no "discard edits?" prompt)
                v.undo()
            if v.dirty():
                errors.append('world: undo did not restore the world')
        else:
            errors.append('world: chair not in 설치물 list')
    else:
        errors.append('world: 64_0.bwr not listed')
    if len(sys.argv) > 3:                      # optional: a world save (.bwr) to open as a file
        page.saved = [{'path': os.path.abspath(sys.argv[3]), 'name': os.path.basename(sys.argv[3]),
                       'folder': 'Local', 'label': '현재 월드', 'mtime': time.time(), 'size': 0}]
        page.fill()
        hit = page.list.findItems(os.path.basename(sys.argv[3]), Qt.MatchContains)
        if hit:
            page.list.itemClicked.emit(hit[0])
            pump(8)
            win.grab().save(os.path.join(shots, 'world_save_opened.png'))
            if not (page.doc and page.doc.path):
                errors.append('world: save file did not open')
        else:
            errors.append('world: save file not listed')
except Exception:
    errors.append('interaction: ' + traceback.format_exc())

win.close()
pump(0.5, settle=False)
for e in errors:
    print('ERROR', e)
print('UI SMOKE', 'OK' if not errors else f'FAILED ({len(errors)})')
sys.exit(1 if errors else 0)
