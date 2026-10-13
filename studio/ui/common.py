"""Shared Qt helpers: background tasks, dialogs, image conversion, theme."""
from __future__ import annotations

import io
import os
import traceback

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QSizePolicy, QWidget)

from ..core import StudioError

DARK = {
    'bg': '#15171c', 'bg2': '#1c1f26', 'bg3': '#242832', 'line': '#313644', 'text': '#e6e8ee',
    'dim': '#9aa1b2', 'faint': '#6b7283', 'accent': '#5fb36b', 'accent2': '#3d8a49', 'warn': '#e0a84a',
    'bad': '#e06464', 'new': '#5aa2e0',
}

STYLE = '''
QWidget { background: %(bg)s; color: %(text)s; font-family: "Segoe UI", "Malgun Gothic"; font-size: 10pt; }
QMainWindow::separator { background: %(line)s; width: 1px; }
QToolTip { background: %(bg3)s; color: %(text)s; border: 1px solid %(line)s; }
QPushButton { background: %(bg3)s; border: 1px solid %(line)s; border-radius: 5px; padding: 5px 11px; }
QPushButton:hover { border-color: #4a5164; background: #2b303b; }
QPushButton:pressed { background: #20242c; }
QPushButton:disabled { color: %(faint)s; }
QPushButton[primary="true"] { background: %(accent2)s; border-color: %(accent2)s; color: white; }
QPushButton[primary="true"]:hover { background: %(accent)s; }
QPushButton[danger="true"] { color: %(bad)s; }
QPushButton:checked { background: %(accent2)s; color: white; border-color: %(accent2)s; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: %(bg3)s; border: 1px solid %(line)s; border-radius: 5px; padding: 4px 7px; selection-background-color: #2c4a63; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: %(accent2)s; }
QComboBox QAbstractItemView { background: %(bg3)s; selection-background-color: %(accent2)s; }
QTreeWidget, QTreeView, QListWidget, QTableWidget, QTableView {
    background: %(bg)s; border: none; alternate-background-color: #191b21; outline: 0; }
QTreeWidget::item, QListWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {
    background: #2a3a2d; color: %(text)s; }
QTreeWidget::item:hover, QListWidget::item:hover { background: %(bg3)s; }
QHeaderView::section { background: %(bg2)s; color: %(dim)s; border: none; border-bottom: 1px solid %(line)s; padding: 4px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: %(bg2)s; color: %(dim)s; padding: 7px 14px; border: none; }
QTabBar::tab:selected { color: %(text)s; border-bottom: 2px solid %(accent)s; }
QScrollBar:vertical { background: %(bg)s; width: 11px; } QScrollBar::handle:vertical { background: #353b48; border-radius: 5px; min-height: 30px; }
QScrollBar:horizontal { background: %(bg)s; height: 11px; } QScrollBar::handle:horizontal { background: #353b48; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: %(line)s; }
QStatusBar { background: %(bg2)s; color: %(dim)s; border-top: 1px solid %(line)s; }
QGroupBox { border: 1px solid %(line)s; border-radius: 7px; margin-top: 14px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: %(dim)s; }
QSlider::groove:horizontal { height: 4px; background: %(line)s; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -6px 0; border-radius: 7px; background: %(accent)s; }
QProgressBar { background: %(bg3)s; border: 1px solid %(line)s; border-radius: 4px; text-align: center; height: 14px; }
QProgressBar::chunk { background: %(accent2)s; }
QListWidget#nav { background: %(bg2)s; border-right: 1px solid %(line)s; padding: 6px; font-size: 10.5pt; }
QListWidget#nav::item { padding: 9px 10px; border-radius: 6px; }
QListWidget#nav::item:selected { background: %(accent2)s; color: white; }
QLabel[muted="true"] { color: %(dim)s; } QLabel[faint="true"] { color: %(faint)s; }
QLabel[title="true"] { font-weight: 600; font-size: 11pt; }
QFrame#card { background: %(bg2)s; border: 1px solid %(line)s; border-radius: 8px; }
QFrame#head { background: %(bg2)s; border-bottom: 1px solid %(line)s; }
''' % DARK


def apply_theme(app: QApplication):
    app.setStyle('Fusion')
    pal = QPalette()
    for role, key in ((QPalette.Window, 'bg'), (QPalette.Base, 'bg3'), (QPalette.AlternateBase, 'bg2'),
                      (QPalette.Text, 'text'), (QPalette.WindowText, 'text'), (QPalette.Button, 'bg3'),
                      (QPalette.ButtonText, 'text'), (QPalette.Highlight, 'accent2'),
                      (QPalette.HighlightedText, 'text'), (QPalette.ToolTipBase, 'bg3'), (QPalette.ToolTipText, 'text')):
        pal.setColor(role, QColor(DARK[key]))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)


# --------------------------------------------------------------------------- background tasks

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)


class Task(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()

    def run(self):
        try:
            res = self.fn(*self.args)
        except StudioError as ex:
            self.signals.failed.emit(str(ex))
        except Exception as ex:
            traceback.print_exc()
            self.signals.failed.emit(f'{ex.__class__.__name__}: {ex}')
        else:
            self.signals.done.emit(res)


_tasks = set()


def run_task(fn, *args, done=None, failed=None, busy: str | None = None, progress: bool = False):
    """Run fn(*args) in the thread pool; done(result) / failed(message) run on the GUI thread.
    With progress=True fn receives progress(done, total, message) as its first argument."""
    on_done, on_error = done, failed
    win = main_window()
    task = Task(fn, *args)
    if progress:
        task.args = (lambda d, t, m='': task.signals.progress.emit(d, t, m),) + args
        if win:
            task.signals.progress.connect(win.show_progress)
    if busy and win:
        win.busy(busy, True)

    def finish():
        _tasks.discard(task)
        if busy and win:
            win.busy(busy, False)

    def ok(res):
        finish()
        if on_done:
            on_done(res)

    def bad(msg):
        finish()
        (on_error or (lambda m: error(m)))(msg)
    task.signals.done.connect(ok)
    task.signals.failed.connect(bad)
    _tasks.add(task)
    QThreadPool.globalInstance().start(task)
    return task


def guarded(fn):
    """Decorator for slots: show StudioError / exceptions in a message box instead of crashing."""
    def wrap(*a, **kw):
        try:
            return fn(*a, **kw)
        except StudioError as ex:
            error(str(ex))
        except Exception as ex:
            traceback.print_exc()
            error(f'{ex.__class__.__name__}: {ex}')
    wrap.__name__ = fn.__name__
    return wrap


# --------------------------------------------------------------------------- dialogs & messages

def main_window():
    for w in QApplication.topLevelWidgets():
        if w.objectName() == 'main':
            return w
    return None


def error(msg: str, title: str = '오류'):
    QMessageBox.warning(main_window(), title, msg)


def info(msg: str, title: str = '알림'):
    QMessageBox.information(main_window(), title, msg)


def toast(msg: str, ms: int = 5000):
    w = main_window()
    if w:
        w.statusBar().showMessage(msg.replace('\n', '  '), ms)


def confirm(title: str, text: str, ok: str = '확인', danger: bool = False) -> bool:
    box = QMessageBox(main_window())
    box.setWindowTitle(title)
    box.setText(text)
    b_ok = box.addButton(ok, QMessageBox.AcceptRole)
    box.addButton('취소', QMessageBox.RejectRole)
    if danger:
        b_ok.setProperty('danger', True)
    box.exec()
    return box.clickedButton() is b_ok


def ask_text(title: str, label: str, value: str = '') -> str | None:
    text, ok = QInputDialog.getText(main_window(), title, label, text=value)
    return text.strip() if ok and text.strip() else None


IMAGE_FILTER = '이미지 (*.png *.jpg *.jpeg *.tga *.bmp);;모든 파일 (*.*)'


def open_file(title: str, filt: str = '모든 파일 (*.*)', start: str = '') -> str | None:
    p, _ = QFileDialog.getOpenFileName(main_window(), title, start, filt)
    return p or None


def save_file(title: str, name: str = '', filt: str = '모든 파일 (*.*)') -> str | None:
    p, _ = QFileDialog.getSaveFileName(main_window(), title, name, filt)
    return p or None


def pick_folder(title: str, start: str = '') -> str | None:
    p = QFileDialog.getExistingDirectory(main_window(), title, start)
    return p or None


def open_in_shell(path: str):
    """Explorer for folders, the default editor for media/text files (never executables)."""
    safe = {'.png', '.jpg', '.jpeg', '.tga', '.bmp', '.gif', '.dds', '.wav', '.ogg', '.mp3', '.ttf', '.otf',
            '.xml', '.txt', '.fx', '.fxh', '.hlsl', '.ps', '.vs', '.shader', '.scene', '.effect', '.nwmt',
            '.nwat', '.nwbt', '.glb', '.gltf', '.vox', '.json', '.zip'}
    if not path or not os.path.exists(path):
        return False
    if os.path.isfile(path) and os.path.splitext(path)[1].lower() not in safe:
        return False
    os.startfile(path)  # noqa: S606 - folder or non-executable file
    return True


# --------------------------------------------------------------------------- images

def pil_to_qimage(im: Image.Image) -> QImage:
    im = im.convert('RGBA')
    data = im.tobytes('raw', 'RGBA')
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888)
    return q.copy()


def pixmap_from_bytes(data: bytes) -> QPixmap:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return QPixmap()
    return QPixmap.fromImage(pil_to_qimage(im))


def checker_pixmap(pm: QPixmap, cell: int = 8) -> QPixmap:
    out = QPixmap(pm.size())
    p = QPainter(out)
    a, b = QColor('#2a2d34'), QColor('#353943')
    for y in range(0, pm.height(), cell):
        for x in range(0, pm.width(), cell):
            p.fillRect(x, y, cell, cell, a if (x // cell + y // cell) % 2 == 0 else b)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def scaled(pm: QPixmap, max_w: int, max_h: int, pixel: bool = False) -> QPixmap:
    if pm.isNull():
        return pm
    if pm.width() <= 128 and pm.height() <= 256 and pixel:
        f = max(1, min(max_w // max(pm.width(), 1), max_h // max(pm.height(), 1), 4))
        return pm.scaled(pm.width() * f, pm.height() * f, Qt.KeepAspectRatio, Qt.FastTransformation)
    if pm.width() > max_w or pm.height() > max_h:
        return pm.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm


def fmt_size(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    if n < 1024 ** 2:
        return f'{n / 1024:.1f} KB'
    if n < 1024 ** 3:
        return f'{n / 1024 ** 2:.1f} MB'
    return f'{n / 1024 ** 3:.2f} GB'


# --------------------------------------------------------------------------- small widgets

def button(text: str, slot=None, primary=False, danger=False, tip: str | None = None, checkable=False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty('primary', True)
    if danger:
        b.setProperty('danger', True)
    if tip:
        b.setToolTip(tip)
    if checkable:
        b.setCheckable(True)
    if slot:
        if checkable:
            b.toggled.connect(slot)
        else:
            g = guarded(slot)
            b.clicked.connect(lambda _checked=False: g())
    return b


def label(text: str = '', muted=False, faint=False, title=False, wrap=False, selectable=False) -> QLabel:
    lb = QLabel(text)
    for k, v in (('muted', muted), ('faint', faint), ('title', title)):
        if v:
            lb.setProperty(k, True)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


BADGE_COLORS = {'modified': ('수정됨', DARK['warn']), 'new': ('새 파일', DARK['new']),
                'merged': ('항목 추가', DARK['accent']), 'original': ('원본', DARK['faint'])}


def badge(status: str) -> QLabel:
    text, color = BADGE_COLORS.get(status, (status, DARK['faint']))
    lb = QLabel(text)
    lb.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: 8px; padding: 1px 7px; font-size: 8.5pt;')
    lb.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return lb


def hbox(*widgets, spacing: int = 6, margins=(0, 0, 0, 0), stretch_last=False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            continue
        if x == 'stretch':
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def debounce(ms: int, fn):
    t = QTimer()
    t.setSingleShot(True)
    t.setInterval(ms)
    t.timeout.connect(fn)
    return t
