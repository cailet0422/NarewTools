"""NarewModManager entry point: install, order and apply Islet Online mods (standalone, native Qt)."""
from __future__ import annotations

import os
import sys


def _log_when_frozen():
    """A windowed build has no console; keep tracebacks in a log file."""
    if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
        from narew.paths import manager_local_dir
        os.makedirs(manager_local_dir(), exist_ok=True)
        f = open(os.path.join(manager_local_dir(), 'log.txt'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = f


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    _log_when_frozen()
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from studio.ui.common import apply_theme

    from .core import Manager
    from .ui import ModWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName('NarewModManager')
    apply_theme(app)
    mgr = Manager()
    win = ModWindow(mgr)
    if '--selftest' in argv:          # packaging check
        print('selftest OK', flush=True)
        return 0
    # one window only: two would each keep their own idea of what is applied to the game
    lock = QLockFile(os.path.join(mgr.home, 'instance.lock'))
    if not lock.tryLock(300):
        QMessageBox.information(None, 'NarewModManager', 'NarewModManager가 이미 실행 중입니다.\n열려 있는 창을 쓰세요.')
        return 0
    win.show()
    win.updater.start()
    for a in argv:                    # "open with" / drag onto the exe: install those mods
        if os.path.exists(a):
            QTimer.singleShot(200, lambda a=a: win.install(a))
    if not mgr.game_root:
        QTimer.singleShot(300, win.choose_game)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
