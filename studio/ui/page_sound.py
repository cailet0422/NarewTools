"""사운드 편집: waveform view, play, select, cut/copy/paste, fades, volume, speed; save in the original format."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QLineF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QScrollBar, QSplitter,
                               QVBoxLayout, QWidget)

from .. import audio as A
from ..core import StudioError
from .common import (DARK, button, confirm, debounce, error, guarded, hbox, label, open_file, save_file, toast)

SOUND_FILTER = '소리 (*.wav *.ogg *.mp3 *.flac);;모든 파일 (*.*)'
UNDO_SAMPLES = 40_000_000          # keep undo history under ~160 MB of float32 samples


def fmt_time(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60)
    return f'{int(m)}:{s:06.3f}'


class WaveView(QWidget):
    """Waveform of a Clip with zoom, scroll, selection, cursor and playhead."""
    selection_changed = Signal()
    view_changed = Signal()

    RULER = 20

    def __init__(self):
        super().__init__()
        self.clip: A.Clip | None = None
        self.start = 0                # first frame shown
        self.spp = 1.0                # frames per pixel
        self.sel: tuple[int, int] | None = None
        self.cursor = 0
        self.play_pos = None
        self._anchor = None
        self.setMinimumHeight(220)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    # -- geometry
    def visible_frames(self) -> int:
        return int(self.spp * max(1, self.width()))

    def frame_at(self, x: float) -> int:
        if not self.clip:
            return 0
        return int(max(0, min(self.clip.frames, self.start + x * self.spp)))

    def x_of(self, frame: float) -> float:
        return (frame - self.start) / self.spp

    def set_clip(self, clip: A.Clip | None, keep_view: bool = False):
        self.clip = clip
        if not keep_view:
            self.sel, self.cursor = None, 0
            self.fit()
        else:
            self.clamp()
            if self.sel and clip:
                a, b = self.sel
                self.sel = (min(a, clip.frames), min(b, clip.frames)) if min(b, clip.frames) > min(a, clip.frames) else None
            self.cursor = min(self.cursor, clip.frames if clip else 0)
        self.update()
        self.view_changed.emit()

    def fit(self):
        if self.clip:
            self.spp = max(self.clip.frames / max(1, self.width()), 1 / 16)
        self.start = 0
        self.update()
        self.view_changed.emit()

    def clamp(self):
        if not self.clip:
            return
        self.spp = max(1 / 16, min(self.spp, max(1.0, self.clip.frames / max(1, self.width()))))
        self.start = int(max(0, min(self.start, self.clip.frames - self.visible_frames())))

    def zoom(self, factor: float, at_x: float | None = None):
        if not self.clip:
            return
        at_x = self.width() / 2 if at_x is None else at_x
        f = self.start + at_x * self.spp
        self.spp *= factor
        self.clamp()
        self.start = int(max(0, f - at_x * self.spp))
        self.clamp()
        self.update()
        self.view_changed.emit()

    def selection(self) -> tuple[int, int] | None:
        if self.sel and self.sel[1] > self.sel[0]:
            return self.sel
        return None

    # -- input
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ShiftModifier:
            self.start = int(self.start - e.angleDelta().y() / 120 * self.visible_frames() * 0.1)
            self.clamp()
            self.update()
            self.view_changed.emit()
        else:
            self.zoom(0.8 if e.angleDelta().y() > 0 else 1.25, e.position().x())

    def mousePressEvent(self, e):
        if not self.clip or e.button() != Qt.LeftButton:
            return
        f = self.frame_at(e.position().x())
        if e.modifiers() & Qt.ShiftModifier:
            a = self.sel[0] if self.sel else self.cursor
            self.sel = (min(a, f), max(a, f))
        else:
            self._anchor = f
            self.sel = None
            self.cursor = f
        self.update()
        self.selection_changed.emit()

    def mouseMoveEvent(self, e):
        if self._anchor is not None and e.buttons() & Qt.LeftButton:
            f = self.frame_at(e.position().x())
            self.sel = (min(self._anchor, f), max(self._anchor, f))
            self.update()
            self.selection_changed.emit()

    def mouseReleaseEvent(self, e):
        self._anchor = None

    def mouseDoubleClickEvent(self, e):
        if self.clip:
            self.sel = (0, self.clip.frames)
            self.update()
            self.selection_changed.emit()

    # -- drawing
    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor('#101318'))
        if not self.clip or self.clip.frames == 0:
            p.setPen(QColor(DARK['faint']))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, '왼쪽에서 소리를 고르세요')
            return
        c = self.clip
        # ruler
        p.fillRect(0, 0, w, self.RULER, QColor('#181c23'))
        secs = self.visible_frames() / c.rate
        step = next((s for s in (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)
                     if secs / s <= max(2, w / 90)), 120)
        t = (self.start / c.rate // step) * step
        p.setPen(QColor(DARK['dim']))
        while t * c.rate < self.start + self.visible_frames():
            x = self.x_of(t * c.rate)
            if x >= 0:
                p.drawLine(QLineF(x, self.RULER - 6, x, self.RULER))
                p.drawText(QRectF(x + 3, 0, 90, self.RULER - 4), Qt.AlignLeft | Qt.AlignVCenter, fmt_time(t))
            t += step
        # selection
        top = self.RULER
        lane = (h - top) / c.channels
        s = self.selection()
        if s:
            x0, x1 = self.x_of(s[0]), self.x_of(s[1])
            p.fillRect(QRectF(x0, top, max(1.0, x1 - x0), h - top), QColor(70, 120, 200, 70))
        # waveform
        lo, hi = A.peaks(c, self.start, self.visible_frames(), w)
        for ch in range(c.channels):
            mid = top + lane * (ch + 0.5)
            amp = lane * 0.46
            p.setPen(QPen(QColor('#2a3140'), 1))
            p.drawLine(QLineF(0, mid, w, mid))
            p.setPen(QPen(QColor(DARK['accent']), 1))
            n = min(w, lo.shape[1])
            lines = [QLineF(x + 0.5, mid - hi[ch, x] * amp, x + 0.5, mid - lo[ch, x] * amp + 0.01) for x in range(n)]
            p.drawLines(lines)
            if ch:
                p.setPen(QPen(QColor('#313644'), 1))
                p.drawLine(QLineF(0, top + lane * ch, w, top + lane * ch))
        # cursor / playhead
        p.setPen(QPen(QColor('#f0f0f0'), 1))
        x = self.x_of(self.cursor)
        p.drawLine(QLineF(x, top, x, h))
        if self.play_pos is not None:
            p.setPen(QPen(QColor('#ffe14a'), 2))
            x = self.x_of(self.play_pos)
            p.drawLine(QLineF(x, top, x, h))


class SoundPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.name = None
        self.ext = '.wav'
        self.clip: A.Clip | None = None
        self.undo_stack, self.redo_stack = [], []
        self.saved_depth = 0          # len(undo_stack) at the last load/save
        self.clipboard: np.ndarray | None = None
        self.sink = None
        self.buffer = None
        self.play_from = 0
        self.play_end = 0
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._tick)
        # left: sounds
        self.search = QLineEdit()
        self.search.setPlaceholderText('소리 검색 (예: ui, bgm, step)')
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda it: self.open_sound(it.data(Qt.UserRole)))
        self._t = debounce(150, self.fill)
        self.search.textChanged.connect(lambda: self._t.start())
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(self.search)
        ll.addWidget(self.list, 1)
        ll.addWidget(label('게임의 소리 파일(WAV·OGG·MP3). 저장하면 원래 형식·샘플레이트·채널 그대로 모드에 들어갑니다.',
                           faint=True, wrap=True))
        # center
        self.title = label('소리를 고르세요', title=True)
        self.meta = label('', muted=True)
        self.selinfo = label('', muted=True)
        self.wave = WaveView()
        self.wave.selection_changed.connect(self._update_info)
        self.wave.view_changed.connect(self._sync_scroll)
        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)
        self.play_btn = button('▶ 재생', self.toggle_play, primary=True, tip='Space — 선택 구간, 없으면 커서부터')
        self.undo_btn = button('↶ 되돌리기', self.undo, tip='Ctrl+Z')
        self.redo_btn = button('↷ 다시', self.redo, tip='Ctrl+Y')
        self.save_btn = button('💾 모드에 저장', self.save, primary=True)
        b = button
        row1 = hbox(self.play_btn, b('⏮ 처음', self.to_start), b('전체 선택', self.select_all, tip='Ctrl+A / 더블클릭'),
                    b('＋', lambda: self.wave.zoom(0.5), tip='확대 (휠)'), b('－', lambda: self.wave.zoom(2), tip='축소'),
                    b('맞춤', self.wave.fit), 'stretch', self.undo_btn, self.redo_btn, margins=(10, 0, 10, 4))
        row2 = hbox(b('✂ 잘라내기', self.cut_sel, tip='Ctrl+X / Delete'), b('복사', self.copy_sel, tip='Ctrl+C'),
                    b('붙여넣기', self.paste, tip='Ctrl+V — 커서 위치에 끼워 넣기'),
                    b('선택만 남기기', lambda: self.edit(lambda c, a, z: A.keep(c, a, z), need_sel=True)),
                    b('무음', lambda: self.edit(A.silence)),
                    b('페이드 인', lambda: self.edit(lambda c, a, z: A.fade(c, a, z, True))),
                    b('페이드 아웃', lambda: self.edit(lambda c, a, z: A.fade(c, a, z, False))),
                    b('볼륨…', self.ask_gain), b('정규화', lambda: self.edit(A.normalize)),
                    b('거꾸로', lambda: self.edit(A.reverse)), b('속도…', self.ask_speed), 'stretch',
                    margins=(10, 0, 10, 4))
        row3 = hbox(b('파일에서 가져오기…', self.import_file, tip='다른 소리 파일로 통째로 바꿉니다 (샘플레이트·채널은 원래대로 맞춤)'),
                    b('내보내기…', self.export_file, tip='WAV / OGG / MP3 파일로 저장'), 'stretch',
                    b('원본으로', self.revert, danger=True, tip='이 소리의 모드 수정본을 지웁니다'), self.save_btn,
                    margins=(10, 0, 10, 6))
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(hbox(self.title, 'stretch', self.meta, margins=(10, 6, 10, 4)))
        cl.addWidget(row1)
        cl.addWidget(row2)
        cl.addWidget(row3)
        cl.addWidget(self.wave, 1)
        cl.addWidget(self.scroll)
        cl.addWidget(hbox(self.selinfo, 'stretch', label('드래그: 구간 선택 · Shift+클릭: 넓히기 · 휠: 확대 · Shift+휠: 이동',
                                                        faint=True), margins=(10, 4, 10, 6)))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.setSizes([280, 1200])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self._update_buttons()

    # ------------------------------------------------------------------ list
    def on_show(self, name: str | None = None):
        if not self.st.game:
            return
        self.fill()
        if name:
            self.open_sound(name)

    def on_state(self):
        if self.isVisible():
            self.fill()

    def sounds(self) -> list[str]:
        names = {k: loc.entry.name for k, loc in self.st.game.index.items() if k.endswith(A.EXTS)}
        for k, (n, _) in self.st.project_files().items():
            if k.endswith(A.EXTS):
                names[k] = n
        return [names[k] for k in sorted(names)]

    def fill(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for n in self.sounds():
            if q and q not in n.lower():
                continue
            mod = self.st.status_of(n) != 'original'
            it = QListWidgetItem(n + ('  ●' if mod else ''))
            it.setData(Qt.UserRole, n)
            self.list.addItem(it)

    # ------------------------------------------------------------------ open / save
    def dirty(self) -> bool:
        return self.clip is not None and len(self.undo_stack) != self.saved_depth

    def can_leave(self) -> bool:
        if self.dirty():
            return confirm('저장하지 않은 변경', f'{self.name}의 변경을 버릴까요?', '버리기', danger=True)
        return True

    @guarded
    def open_sound(self, name: str):
        if self.name and name != self.name and not self.can_leave():
            return
        self.stop()
        clip = A.decode(self.st.read(name))
        self.name, self.ext, self.clip = name, os.path.splitext(name)[1].lower(), clip
        self.undo_stack, self.redo_stack, self.saved_depth = [], [], 0
        self.wave.set_clip(clip)
        self.title.setText(f'{name}  · {self.st.status_of(name)}')
        self._update_info()
        self._update_buttons()

    @guarded
    def save(self):
        if not self.clip or not self.mw.require_project():
            return
        self.stop()
        self.st.write(self.name, A.encode(self.clip, self.ext))
        self.saved_depth = len(self.undo_stack)
        self.title.setText(f'{self.name}  · {self.st.status_of(self.name)}')
        self.mw.refresh_header()
        self.fill()
        self._update_buttons()
        toast(f'모드에 저장했습니다: {self.name}')

    @guarded
    def revert(self):
        if not self.name or not self.st.project or self.st.status_of(self.name) == 'original':
            toast('모드에서 바꾼 적이 없는 소리입니다')
            return
        if confirm('원본으로', f'{self.name}의 모드 수정본을 지우고 원본으로 돌릴까요?', '원본으로', danger=True):
            self.st.revert(self.name)
            self.saved_depth = len(self.undo_stack)
            name, self.name = self.name, None
            self.mw.refresh_header()
            self.open_sound(name)
            self.fill()

    @guarded
    def import_file(self):
        if not self.clip:
            return
        p = open_file('가져올 소리 파일', SOUND_FILTER)
        if not p:
            return
        with open(p, 'rb') as f:
            other = A.decode(f.read())
        new = A.conform(other, self.clip.rate, self.clip.channels)
        self.push(new, keep_view=False)
        toast(f'가져왔습니다: {os.path.basename(p)} ({fmt_time(new.duration)}, {new.rate} Hz, {new.channels}채널로 맞춤)')

    @guarded
    def export_file(self):
        if not self.clip:
            return
        stem = os.path.splitext(os.path.basename(self.name))[0]
        p = save_file('내보내기', stem + self.ext, 'WAV (*.wav);;OGG (*.ogg);;MP3 (*.mp3)')
        if not p:
            return
        ext = os.path.splitext(p)[1].lower() or self.ext
        if ext not in A.FORMATS:
            raise StudioError('wav, ogg, mp3 중 하나로 저장하세요')
        with open(p, 'wb') as f:
            f.write(A.encode(self.clip, ext))
        toast(f'내보냈습니다: {p}')

    # ------------------------------------------------------------------ edits
    def push(self, new: A.Clip, keep_view: bool = True):
        self.undo_stack.append(self.clip)
        self.redo_stack.clear()
        total = sum(c.frames * c.channels for c in self.undo_stack)
        while len(self.undo_stack) > 1 and total > UNDO_SAMPLES:
            old = self.undo_stack.pop(0)
            total -= old.frames * old.channels
            self.saved_depth -= 1                 # the saved state may now be unreachable: stays dirty
        self.clip = new
        self.wave.set_clip(new, keep_view=keep_view)
        self._update_info()
        self._update_buttons()

    def _span(self, need_sel: bool = False):
        s = self.wave.selection()
        if s:
            return s
        if need_sel:
            toast('먼저 구간을 드래그해서 고르세요')
            return None
        return 0, self.clip.frames

    @guarded
    def edit(self, fn, need_sel: bool = False):
        if not self.clip:
            return
        span = self._span(need_sel)
        if span is None:
            return
        self.stop()
        self.push(fn(self.clip, *span))

    def ask_gain(self):
        if not self.clip:
            return
        db, ok = QInputDialog.getDouble(self, '볼륨', '바꿀 크기 (dB, +는 크게 −는 작게):', 3.0, -40, 40, 1)
        if ok:
            self.edit(lambda c, a, z: A.gain(c, a, z, db))

    def ask_speed(self):
        if not self.clip:
            return
        f, ok = QInputDialog.getDouble(self, '속도', '배속 (2 = 두 배 빠르고 높게, 0.5 = 느리고 낮게):', 1.25, 0.25, 4.0, 2)
        if ok:
            self.stop()
            self.push(A.speed(self.clip, f), keep_view=False)

    def cut_sel(self):
        s = self._span(need_sel=True)
        if s:
            self.stop()
            new, self.clipboard = A.cut(self.clip, *s)
            self.wave.cursor = s[0]
            self.wave.sel = None
            self.push(new)

    def copy_sel(self):
        s = self._span(need_sel=True)
        if s:
            self.clipboard = self.clip.data[s[0]:s[1]].copy()
            toast(f'복사했습니다 ({fmt_time((s[1] - s[0]) / self.clip.rate)})')

    def paste(self):
        if self.clip is None or self.clipboard is None or not len(self.clipboard):
            toast('붙여넣을 구간이 없습니다 (먼저 복사하거나 잘라내세요)')
            return
        self.stop()
        at = self.wave.cursor
        self.push(A.insert(self.clip, at, self.clipboard))
        self.wave.sel = (at, at + len(self.clipboard))
        self.wave.update()
        self._update_info()

    def select_all(self):
        if self.clip:
            self.wave.sel = (0, self.clip.frames)
            self.wave.update()
            self._update_info()

    def undo(self):
        if self.undo_stack:
            self.stop()
            self.redo_stack.append(self.clip)
            self.clip = self.undo_stack.pop()
            self.wave.set_clip(self.clip, keep_view=True)
            self._update_info()
            self._update_buttons()

    def redo(self):
        if self.redo_stack:
            self.stop()
            self.undo_stack.append(self.clip)
            self.clip = self.redo_stack.pop()
            self.wave.set_clip(self.clip, keep_view=True)
            self._update_info()
            self._update_buttons()

    def keyPressEvent(self, e):
        ctrl = e.modifiers() & Qt.ControlModifier
        k = e.key()
        if k == Qt.Key_Space:
            self.toggle_play()
        elif ctrl and k == Qt.Key_Z:
            self.undo()
        elif ctrl and k == Qt.Key_Y:
            self.redo()
        elif ctrl and k == Qt.Key_X or k == Qt.Key_Delete:
            self.cut_sel()
        elif ctrl and k == Qt.Key_C:
            self.copy_sel()
        elif ctrl and k == Qt.Key_V:
            self.paste()
        elif ctrl and k == Qt.Key_A:
            self.select_all()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ playback
    def toggle_play(self):
        if self.sink is not None:
            self.stop()
        else:
            self.play()

    @guarded
    def play(self):
        if not self.clip:
            return
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
        s = self.wave.selection()
        a, b = s if s else (self.wave.cursor if self.wave.cursor < self.clip.frames else 0, self.clip.frames)
        fmt = QAudioFormat()
        fmt.setSampleRate(self.clip.rate)
        fmt.setChannelCount(self.clip.channels)
        fmt.setSampleFormat(QAudioFormat.Int16)
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            error('소리를 낼 장치가 없습니다')
            return
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(A.pcm16(self.clip, a, b)))
        self.buffer.open(QIODevice.ReadOnly)
        self.sink = QAudioSink(dev, fmt, self)
        self.play_from, self.play_end = a, b
        self.sink.start(self.buffer)
        if self.sink.error() != QAudio.NoError:
            self.stop()
            error('이 소리 형식을 재생할 수 없습니다')
            return
        self.play_btn.setText('■ 정지')
        self.timer.start()

    def stop(self):
        self.timer.stop()
        if self.sink is not None:
            self.sink.stop()
            self.sink.deleteLater()
            self.sink = None
        if self.buffer is not None:
            self.buffer.close()
            self.buffer = None
        self.wave.play_pos = None
        self.wave.update()
        self.play_btn.setText('▶ 재생')

    def _tick(self):
        if self.sink is None or not self.clip:
            return
        pos = self.play_from + int(self.sink.processedUSecs() / 1e6 * self.clip.rate)
        self.wave.play_pos = min(pos, self.play_end)
        self.wave.update()
        from PySide6.QtMultimedia import QAudio
        if pos >= self.play_end or self.sink.state() in (QAudio.IdleState, QAudio.StoppedState):
            self.stop()

    def shutdown(self):
        self.stop()

    def to_start(self):
        self.wave.cursor = 0
        self.wave.sel = None
        self.wave.start = 0
        self.wave.update()
        self._update_info()

    # ------------------------------------------------------------------ status
    def _sync_scroll(self):
        c = self.clip
        self.scroll.blockSignals(True)
        if c:
            vis = self.wave.visible_frames()
            self.scroll.setRange(0, max(0, c.frames - vis))
            self.scroll.setPageStep(max(1, vis))
            self.scroll.setSingleStep(max(1, vis // 20))
            self.scroll.setValue(self.wave.start)
        else:
            self.scroll.setRange(0, 0)
        self.scroll.blockSignals(False)

    def _scrolled(self, v):
        self.wave.start = v
        self.wave.update()

    def _update_info(self):
        c = self.clip
        if not c:
            self.meta.setText('')
            self.selinfo.setText('')
            return
        self.meta.setText(f'{self.ext[1:].upper()} · {c.rate} Hz · {c.channels}채널 · {fmt_time(c.duration)}'
                          + (f' · {c.subtype}' if c.subtype else ''))
        s = self.wave.selection()
        self.selinfo.setText(f'선택 {fmt_time(s[0] / c.rate)} – {fmt_time(s[1] / c.rate)} ({fmt_time((s[1] - s[0]) / c.rate)})'
                             if s else f'커서 {fmt_time(self.wave.cursor / c.rate)} · 선택 없음 (편집은 전체에 적용)')

    def _update_buttons(self):
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.redo_btn.setEnabled(bool(self.redo_stack))
        self.save_btn.setEnabled(self.dirty())
