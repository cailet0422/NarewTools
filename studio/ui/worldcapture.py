"""월드 캡처 창: a small always-on-top window that copies the world you are playing to a .bwr file —
by button, by the global shortcut Ctrl+Alt+S (works while the game has focus), or automatically
every time the game saves.  It only reads the game's own save (studio/savedworlds.py); nothing is
injected into the game."""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from .. import savedworlds
from .common import DARK, button, hbox, label, open_in_shell, run_task

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_S = 0x53
HOTKEY_ID = 0x4E57
HOTKEY_TEXT = 'Ctrl+Alt+S'


def _beep(ok: bool):
    if sys.platform == 'win32':
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK if ok else winsound.MB_ICONHAND)


def _ago(t: float) -> str:
    s = int(time.time() - t)
    if s < 60:
        return f'{s}초 전'
    if s < 3600:
        return f'{s // 60}분 전'
    return f'{s // 3600}시간 전'


class CaptureWindow(QWidget):
    captured = Signal(dict)

    def __init__(self, game_root, dest_dir: str | None = None):
        """game_root: callable returning the game folder (or None)."""
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('월드 캡처 — NarewStudio')
        self.setObjectName('capture')
        self.game_root = game_root
        self.dest_dir = dest_dir or savedworlds.default_capture_dir()
        self.hotkey = False
        self.busy = False
        self.seen_mtime = None
        self.cur_lbl = label('', wrap=True)
        self.result = label('', muted=True, wrap=True, selectable=True)
        self.btn = button(f'📸 지금 저장   ({HOTKEY_TEXT})', self.capture, primary=True)
        self.btn.setMinimumHeight(34)
        self.auto = QCheckBox('게임이 저장할 때마다 자동으로 남기기')
        self.hk_lbl = label('', faint=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self.cur_lbl)
        lay.addWidget(self.btn)
        lay.addWidget(self.auto)
        lay.addWidget(self.result)
        lay.addWidget(hbox(button('저장 폴더 열기', lambda: (os.makedirs(self.dest_dir, exist_ok=True),
                                                         open_in_shell(self.dest_dir))), 'stretch', self.hk_lbl))
        lay.addWidget(label('게임이 마지막으로 저장한 월드를 복사합니다. 내 PC에서 여는 월드(싱글 플레이, 내가 연 서버)만 '
                            '됩니다. 방금 지은 것이 없으면 "자동으로 남기기"를 켜 두거나 게임을 나갔다 오세요.',
                            faint=True, wrap=True))
        self.setFixedWidth(400)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(2000)
        self.poll()

    # ------------------------------------------------------------------ global shortcut
    def showEvent(self, e):
        super().showEvent(e)
        if not self.hotkey and sys.platform == 'win32':
            self.hotkey = bool(ctypes.windll.user32.RegisterHotKey(
                wintypes.HWND(int(self.winId())), HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_S))
            self.hk_lbl.setText(f'단축키 {HOTKEY_TEXT} 켜짐' if self.hotkey
                                else f'{HOTKEY_TEXT}를 다른 프로그램이 쓰고 있어 단축키는 꺼짐')

    def closeEvent(self, e):
        if self.hotkey:
            ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(int(self.winId())), HOTKEY_ID)
            self.hotkey = False
        self.timer.stop()
        super().closeEvent(e)

    def nativeEvent(self, event_type, message):
        if bytes(event_type) == b'windows_generic_MSG':
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.capture(from_hotkey=True)
                return True, 0
        return super().nativeEvent(event_type, message)

    # ------------------------------------------------------------------ state
    def poll(self):
        root = self.game_root()
        cur = savedworlds.current_world(root) if root else None
        if not cur:
            self.cur_lbl.setText('게임 폴더의 Local\\에 월드 저장본이 아직 없습니다.\n싱글 플레이(내 월드)로 들어가면 생깁니다.')
            self.btn.setEnabled(False)
            return
        self.btn.setEnabled(not self.busy)
        self.cur_lbl.setText(f"<b>현재 월드: {cur['name']}</b><br>"
                             f"<span style='color:{DARK['dim']}'>게임이 저장한 시각 "
                             f"{time.strftime('%H:%M:%S', time.localtime(cur['mtime']))} ({_ago(cur['mtime'])})</span>")
        if self.seen_mtime is None:
            self.seen_mtime = cur['mtime']
        elif cur['mtime'] != self.seen_mtime and time.time() - cur['mtime'] > 1.5:
            self.seen_mtime = cur['mtime']          # the game saved again
            if self.auto.isChecked():
                self.capture(auto=True)

    def capture(self, from_hotkey: bool = False, auto: bool = False):
        root = self.game_root()
        if self.busy or not root:
            return
        self.busy = True
        self.btn.setEnabled(False)

        def done(r):
            self.busy = False
            self.btn.setEnabled(True)
            when = time.strftime('%H:%M:%S', time.localtime(r['saved_at']))
            name = os.path.basename(r['path'])
            self.result.setText(('자동으로 ' if auto else '') + (f'저장했습니다: {name}' if r['new']
                                                                 else f'이미 저장한 상태입니다: {name}')
                                + f'\n(게임 저장 {when}, {r["size"]:,} 바이트)')
            if from_hotkey:
                _beep(True)
            self.poll()
            self.captured.emit(r)

        def failed(msg):
            self.busy = False
            self.btn.setEnabled(True)
            self.result.setText('저장하지 못했습니다: ' + msg)
            if from_hotkey or auto:
                _beep(False)
        run_task(savedworlds.capture, root, self.dest_dir, done=done, failed=failed)
