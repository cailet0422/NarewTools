"""NarewStudio entry point: native (Qt) modding tool for Islet Online."""
from __future__ import annotations

import os
import sys


def _log_when_frozen():
    """A windowed build has no console; keep tracebacks in a log file."""
    if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
        from narew.paths import appdata
        f = open(os.path.join(appdata('NarewStudio'), 'log.txt'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = f


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    _log_when_frozen()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .core import Studio
    from .ui.common import apply_theme
    from .ui.glview import set_default_gl_format
    from .ui.mainwindow import MainWindow

    set_default_gl_format()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('NarewStudio')
    apply_theme(app)
    st = Studio()
    st.restore()
    if '--capture' in argv:           # only the small world capture window (for use while playing)
        from .ui.worldcapture import CaptureWindow
        cap = CaptureWindow(lambda: st.game.root if st.game else None)
        cap.show()
        return app.exec()
    win = MainWindow(st)
    if '--selftest' in argv:          # packaging check: build every page and start OpenGL, then quit
        for key in ('resources', 'blocks', 'furniture', 'create', 'gui', 'sound', 'world', 'models', 'explorer', 'project'):
            win.page(key)
        from .ui.modelview import ModelView
        view = ModelView()
        view.resize(64, 64)
        view.show()
        for _ in range(20):
            app.processEvents()
        gl = view.ctx.info.get('GL_RENDERER', '?') if view.ctx else None
        print(f'selftest {"OK" if gl else "FAILED"} (OpenGL: {gl})', flush=True)
        return 0 if gl else 1
    win.show()
    win.updater.start()
    win.open_page(st.settings.get('last_page') or 'resources')
    if not st.game:
        QTimer.singleShot(300, win.require_game)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
