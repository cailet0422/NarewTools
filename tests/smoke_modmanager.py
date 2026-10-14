"""Mod manager UI smoke test against the fake game left by tests/test_modmanager.py.

    python tests/smoke_modmanager.py <scratch dir of test_modmanager>
Opens the window, applies, screenshots, restores.  Only the fake game folder is touched.
"""
import os
import sys
import time
import traceback

SCRATCH = os.path.abspath(sys.argv[1])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
os.environ['USERPROFILE'] = os.path.join(SCRATCH, 'home')    # Documents\NarewStudio: shared settings, mods
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from modmanager.core import Manager  # noqa: E402
from modmanager.ui import ModWindow  # noqa: E402
from studio.ui.common import apply_theme  # noqa: E402

errors, infos = [], []
sys.excepthook = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
QMessageBox.warning = staticmethod(lambda parent, title, msg, *a: errors.append(f'warning dialog: {msg}'))
QMessageBox.information = staticmethod(lambda parent, title, msg, *a: infos.append(msg))
QMessageBox.exec = lambda self: None            # confirm() -> no button clicked -> False


def pump(seconds):
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end or (QThreadPool.globalInstance().activeThreadCount() and time.time() < end + 120):
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


app = QApplication([])
apply_theme(app)
mgr = Manager()
assert mgr.game_root and 'game' in mgr.game_root, 'run tests/test_modmanager.py first (fake game expected)'
win = ModWindow(mgr)
win.show()
pump(0.5)
shots = os.path.join(SCRATCH, 'shots')
os.makedirs(shots, exist_ok=True)
win.grab().save(os.path.join(shots, 'mm_start.png'))
win.list.setCurrentRow(1)
win.tabs.setCurrentIndex(1)
pump(0.3)
win.grab().save(os.path.join(shots, 'mm_merges.png'))
win.move(-1)                                   # reorder through the UI
pump(0.3)
order = [m['id'] for m in mgr.installed()]
# another program (e.g. NarewStudio's "install") switches a mod off: the open window must pick it up
other = Manager()
other.set_order([{'id': m['id'], 'enabled': m['id'] != 'second-mod'} for m in other.installed()])
win.refresh()
seen_off = any(win.list.item(i).data(Qt.UserRole) == 'second-mod' and win.list.item(i).checkState() == Qt.Unchecked
               for i in range(win.list.count()))
other.set_order([{'id': m['id'], 'enabled': True} for m in other.installed()])
win.refresh()
win.apply()
pump(1)
win.grab().save(os.path.join(shots, 'mm_applied.png'))
applied = mgr.status()
win.restore_btn.click()                        # confirm() returns False -> nothing happens
pump(0.3)
still = mgr.status()['applied']
mgr.restore_all()
win.refresh()
pump(0.3)
win.grab().save(os.path.join(shots, 'mm_restored.png'))
# a mod copied into the mods folder by hand shows up after ⟳ 새로고침 (and F5 is the same action)
import shutil  # noqa: E402
before_n = win.list.count()
src = os.path.join(mgr.mods_dir, mgr.installed()[0]['id'])
shutil.copytree(src, os.path.join(mgr.mods_dir, 'hand-copied'))
with open(os.path.join(mgr.mods_dir, 'hand-copied', 'mod.json'), encoding='utf-8') as f:
    mj = f.read()
with open(os.path.join(mgr.mods_dir, 'hand-copied', 'mod.json'), 'w', encoding='utf-8') as f:
    f.write(mj.replace('"id": "%s"' % mgr.installed()[0]['id'], '"id": "hand-copied"', 1))
win.reload_btn.click()
pump(0.3)
reload_ok = win.list.count() == before_n + 1 and any(
    win.list.item(i).data(Qt.UserRole) == 'hand-copied' for i in range(win.list.count()))
shutil.rmtree(os.path.join(mgr.mods_dir, 'hand-copied'))
win.reload_mods()
pump(0.3)
reload_ok = reload_ok and win.list.count() == before_n
checks = [('apply ran', applied['applied'] and applied['in_sync']),
          ('refresh button picks up mods folder changes', reload_ok),
          ('report shown', any('표 병합' in i for i in infos)),
          ('reorder saved', order[0] == 'creator-test'),
          ('outside change shown', seen_off),
          ('restore asks first', still),
          ('restored', not mgr.status()['applied'])]
win.close()
for e in errors:
    print('ERROR', e)
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('report:', infos[:1])
bad = errors or [n for n, ok in checks if not ok]
print('MM UI SMOKE', 'OK' if not bad else 'FAILED')
sys.exit(1 if bad else 0)
