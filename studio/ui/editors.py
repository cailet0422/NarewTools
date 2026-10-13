"""Type-specific viewer/editor for one resource (used by the resource and furniture pages)."""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, Qt, QUrl, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QFrame, QGridLayout, QGroupBox, QLabel, QPlainTextEdit,
                               QScrollArea, QSlider, QTextEdit, QVBoxLayout, QWidget)

from .. import imaging, svc_resources as R
from ..core import Studio, StudioError, kind_of
from .codeedit import CodePanel, first_error_line, language_for
from .common import (IMAGE_FILTER, badge, button, checker_pixmap, confirm, error, fmt_size, guarded, hbox, info,
                     label, main_window, open_file, open_in_shell, pil_to_qimage, pixmap_from_bytes,
                     save_file, scaled, toast)

from PySide6.QtGui import QPixmap


class ResourceEditor(QWidget):
    changed = Signal(str)

    def __init__(self, st: Studio, parent=None):
        super().__init__(parent)
        self.st = st
        self.name: str | None = None
        self.code: CodePanel | None = None
        self.dirty = False
        self.setAcceptDrops(True)
        self.head = QFrame()
        self.head.setObjectName('head')
        self.head_lay = QGridLayout(self.head)
        self.head_lay.setContentsMargins(12, 8, 12, 8)
        self.body = QScrollArea()
        self.body.setWidgetResizable(True)
        self.body.setFrameShape(QFrame.NoFrame)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.head)
        lay.addWidget(self.body, 1)
        self.players = []
        self.empty()

    # ------------------------------------------------------------------ chrome
    def _set_body(self, w: QWidget, scroll=True):
        self.body.setWidgetResizable(True)
        old = self.body.takeWidget()
        if old:
            old.deleteLater()
        self.body.setWidget(w)

    def _clear_head(self):
        while self.head_lay.count():
            it = self.head_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def empty(self, msg='왼쪽에서 파일을 고르세요'):
        self.name = None
        self.stop_audio()
        self._clear_head()
        self.head_lay.addWidget(label('리소스', muted=True), 0, 0)
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        for t, kw in ((msg, {'title': True}), ('이미지·사운드·텍스트(XML)·폰트·모델 등 모든 게임 리소스를 수정할 수 있습니다.', {'muted': True}),
                      ('파일을 이 창에 끌어다 놓으면 바로 교체됩니다.', {'faint': True})):
            lb = label(t, **kw)
            lb.setAlignment(Qt.AlignCenter)
            v.addWidget(lb)
        v.addStretch(2)
        self._set_body(w)

    def _render_head(self):
        self._clear_head()
        name = self.name
        st = self.st
        status = st.status_of(name)
        title = label(name, title=True, selectable=True)
        title.setWordWrap(True)
        btns = [button('파일로 교체…', self.replace_dialog), button('내보내기…', self.export_dialog),
                button('외부 편집기로 열기', self.open_external)]
        if status in ('modified', 'new'):
            btns.append(button('원본으로 되돌리기', self.revert, danger=True))
        self.head_lay.addWidget(title, 0, 0)
        self.head_lay.addWidget(badge(status), 0, 1)
        self.head_lay.addWidget(hbox(*btns), 0, 2, alignment=Qt.AlignRight)
        self.head_lay.setColumnStretch(0, 1)

    # ------------------------------------------------------------------ open
    def can_leave(self) -> bool:
        if self.code and self.dirty:
            return confirm('저장하지 않은 변경', '이 파일의 변경 내용을 버릴까요?', '버리기', danger=True)
        return True

    @guarded
    def open(self, name: str):
        self.stop_audio()
        self.code = None
        self.dirty = False
        name = self.st.canonical_name(name)
        self.name = name
        self._render_head()
        kind = kind_of(name)
        if not self.st.exists(name):
            self._set_body(self._missing())
            return
        builder = {'image': self._image, 'audio': self._audio, 'font': self._font, 'text': self._text,
                   'model': self._text, 'brick': self._brick}.get(kind, self._binary)
        try:
            w = builder()
        except StudioError as ex:
            w = label(str(ex), muted=True)
        self._set_body(w)

    def _meta(self) -> QWidget:
        st = self.st
        g = st.game.locate(self.name) if st.game else None
        rows = []
        if g:
            e = g.entry
            rows += [('위치', '팩 밖 파일' if g.pack.startswith('@') else f'{g.pack}.pack'),
                     ('원본 크기', fmt_size(e.raw_size)),
                     ('저장 방식', f'zlib ({fmt_size(e.packed_size)})' if e.compressed else '압축 없음')]
        for k, (n, p) in st.project_files().items():
            if n.lower() == self.name.lower():
                rows += [('수정본 크기', fmt_size(os.path.getsize(p))), ('수정본 위치', p)]
        card = QFrame()
        card.setObjectName('card')
        grid = QGridLayout(card)
        for i, (k, v) in enumerate(rows):
            grid.addWidget(label(k, faint=True), i, 0)
            grid.addWidget(label(v, selectable=True), i, 1)
        grid.setColumnStretch(1, 1)
        return card

    def _missing(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(label('아직 없는 파일입니다. "파일로 교체…"로 새로 넣을 수 있어요.', muted=True))
        v.addStretch(1)
        return w

    # ------------------------------------------------------------------ image
    def _image(self) -> QWidget:
        st, name = self.st, self.name
        status = st.status_of(name)
        w = QWidget()
        v = QVBoxLayout(w)
        row = QWidget()
        h = QGridLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        shows = [('원본', 'game'), ('수정본', 'base')] if status == 'modified' else [('현재', 'base')]
        for col, (title, src) in enumerate(shows):
            data = st.read(name, src)
            try:
                meta = imaging.info(data)
                cap = f'{title} · {meta["width"]}×{meta["height"]} {meta["mode"]}'
            except Exception:
                cap = title
            pm = pixmap_from_bytes(data)
            lb = QLabel()
            lb.setPixmap(checker_pixmap(scaled(pm, 560, 560, pixel=True)))
            h.addWidget(label(cap, muted=True), 0, col)
            h.addWidget(lb, 1, col, alignment=Qt.AlignLeft | Qt.AlignTop)
        h.setColumnStretch(len(shows), 1)
        v.addWidget(row)
        self.resize_box = QCheckBox('교체할 때 원본과 같은 크기로 자동 맞추기')
        self.resize_box.setChecked(True)
        v.addWidget(self.resize_box)
        v.addWidget(label('새 이미지를 여기로 끌어다 놓으면 교체됩니다 (PNG/JPG/TGA/BMP → 원래 형식으로 자동 변환)', faint=True))
        v.addWidget(self._adjust_box())
        v.addWidget(self._meta())
        v.addStretch(1)
        return w

    def _adjust_box(self) -> QWidget:
        box = QGroupBox('🎨 색 조정 (간단 편집)')
        grid = QGridLayout(box)
        self.adj = {'hue': 0.0, 'saturation': 1.0, 'brightness': 1.0, 'contrast': 1.0, 'tint': '#ff8040', 'tint_amount': 0.0}
        preview = QLabel()
        try:
            base = imaging.open_image(self.st.read(self.name))
            base.thumbnail((320, 320))
        except Exception:
            return box

        def refresh():
            out = imaging.adjust(base, **self.adj)
            preview.setPixmap(checker_pixmap(QPixmap.fromImage(pil_to_qimage(out))))
        specs = [('색조', 'hue', -180, 180, 1, lambda x: f'{x:+.0f}°'), ('채도', 'saturation', 0, 200, 100, lambda x: f'{x:.0%}'),
                 ('밝기', 'brightness', 0, 200, 100, lambda x: f'{x:.0%}'), ('대비', 'contrast', 0, 200, 100, lambda x: f'{x:.0%}'),
                 ('색 입히기', 'tint_amount', 0, 100, 100, lambda x: f'{x:.0%}')]
        for r, (txt, key, lo, hi, div, fmt) in enumerate(specs):
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(int(self.adj[key] * div))
            val = label(fmt(self.adj[key]), muted=True)
            def on(vv, key=key, div=div, val=val, fmt=fmt):
                self.adj[key] = vv / div
                val.setText(fmt(self.adj[key]))
                refresh()
            s.valueChanged.connect(on)
            grid.addWidget(label(txt), r, 0)
            grid.addWidget(s, r, 1)
            grid.addWidget(val, r, 2)
        color_btn = button('입힐 색…')
        def pick():
            c = QColorDialog.getColor(parent=self)
            if c.isValid():
                self.adj['tint'] = c.name()
                color_btn.setText(f'입힐 색 {c.name()}')
                refresh()
        color_btn.clicked.connect(pick)
        grid.addWidget(color_btn, len(specs), 1)
        grid.addWidget(button('적용', self.apply_adjust, primary=True), len(specs) + 1, 1, alignment=Qt.AlignLeft)
        grid.addWidget(label('미리보기', muted=True), 0, 3)
        grid.addWidget(preview, 1, 3, len(specs) + 1, 1)
        grid.setColumnStretch(1, 1)
        refresh()
        return box

    def apply_adjust(self):
        if not main_window().require_project():
            return
        R.adjust_apply(self.st, self.name, self.adj)
        toast('색 조정을 적용했습니다')
        self.after_change()

    # ------------------------------------------------------------------ audio
    def stop_audio(self):
        for p in self.players:
            try:
                p.stop()
            except Exception:
                pass
        self.players = []

    def _audio(self) -> QWidget:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        st, name = self.st, self.name
        w = QWidget()
        v = QVBoxLayout(w)
        shows = [('원본', 'game'), ('수정본', 'base')] if st.status_of(name) == 'modified' else [('현재', 'base')]
        cache = os.path.join(st.cache_dir, 'audio')
        os.makedirs(cache, exist_ok=True)
        for title, src in shows:
            data = st.read(name, src)
            fn = os.path.join(cache, f'{src}_{os.path.basename(name.replace(chr(92), "/"))}')
            with open(fn, 'wb') as f:
                f.write(data)
            player = QMediaPlayer(self)
            out = QAudioOutput(self)
            out.setVolume(0.8)
            player.setAudioOutput(out)
            player.setSource(QUrl.fromLocalFile(fn))
            player._out = out
            self.players.append(player)
            pos = QSlider(Qt.Horizontal)
            tlabel = label('0.0 / 0.0초', muted=True)
            player.durationChanged.connect(lambda d, s=pos: s.setRange(0, d))
            player.positionChanged.connect(lambda p, s=pos, t=tlabel, pl=player: (s.blockSignals(True), s.setValue(p),
                                                                                   s.blockSignals(False),
                                                                                   t.setText(f'{p / 1000:.1f} / {pl.duration() / 1000:.1f}초')))
            pos.sliderMoved.connect(player.setPosition)
            card = QFrame()
            card.setObjectName('card')
            cv = QVBoxLayout(card)
            cv.addWidget(label(f'{title} · {fmt_size(len(data))}', muted=True))
            cv.addWidget(hbox(button('▶ 재생', player.play), button('⏸', player.pause), button('■', player.stop), pos, tlabel))
            v.addWidget(card)
        ext = os.path.splitext(name)[1]
        v.addWidget(label(f'같은 형식({ext}) 사운드 파일을 끌어다 놓으면 교체됩니다', faint=True))
        mw = main_window()
        if mw is not None and hasattr(mw, 'open_page'):
            v.addWidget(hbox(button('🎚 사운드 편집기로 열기', lambda: mw.open_page('sound', name=name),
                                    tip='자르기·페이드·볼륨·속도 등을 고칩니다'), 'stretch'))
        v.addWidget(self._meta())
        v.addStretch(1)
        return w

    # ------------------------------------------------------------------ font
    def _font(self) -> QWidget:
        data = self.st.read(self.name)
        fid = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        w = QWidget()
        v = QVBoxLayout(w)
        sample = QTextEdit()
        sample.setPlainText('아일렛 온라인에 오신 것을 환영합니다!\nThe quick brown fox jumps over the lazy dog. 0123456789')
        size = QSlider(Qt.Horizontal)
        size.setRange(8, 72)
        size.setValue(28)

        def upd(s=28):
            f = QFont(fams[0] if fams else 'Segoe UI', s)
            sample.setFont(f)
        size.valueChanged.connect(upd)
        upd()
        v.addWidget(label(f'글꼴: {", ".join(fams) or "미리 볼 수 없음"}', muted=True))
        v.addWidget(hbox(label('크기'), size))
        v.addWidget(sample, 1)
        v.addWidget(label('같은 형식(.ttf/.otf) 폰트 파일을 끌어다 놓으면 교체됩니다', faint=True))
        v.addWidget(self._meta())
        return w

    # ------------------------------------------------------------------ text / model xml
    def _text(self) -> QWidget:
        name = self.name
        text, enc = R.read_text(self.st, name)
        panel = CodePanel()
        panel.editor.set_language(language_for(name))
        panel.editor.setPlainText(text)
        self.code = panel
        dirty_lbl = label('', faint=True)

        def on_change():
            self.dirty = True
            dirty_lbl.setText('● 저장 안 됨')
        panel.editor.textChanged.connect(on_change)
        bar = [button('저장 (Ctrl+S)', lambda: self.save_text(False), primary=True),
               label(f'인코딩 {enc.encoding}{" (BOM)" if enc.bom else ""} · {enc.to_dict()["newline"]} · {len(text):,}자', faint=True),
               dirty_lbl, 'stretch']
        if kind_of(name) == 'model' and name.lower().endswith('.nwmt'):
            bar.insert(1, button('모델 뷰어에서 보기', lambda: main_window().open_page('models', name=name)))
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(hbox(*bar, margins=(8, 6, 8, 6)))
        v.addWidget(panel, 1)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence.Save, panel, activated=lambda: self.save_text(False))
        QShortcut(QKeySequence.Find, panel, activated=lambda: panel.find.setFocus())
        self.dirty = False
        return w

    def save_text(self, force: bool):
        if not self.code or not main_window().require_project():
            return
        try:
            R.save_text(self.st, self.name, self.code.editor.toPlainText(), force)
        except StudioError as ex:
            msg = str(ex)
            line = first_error_line(msg)
            if line:
                self.code.goto_line(line)
            if 'XML' in msg and not force:
                if confirm('XML 오류', msg + '\n\n그래도 저장할까요?', '강제 저장', danger=True):
                    self.save_text(True)
                return
            error(msg)
            return
        self.dirty = False
        toast('저장했습니다')
        self.changed.emit(self.name)
        main_window().refresh_header()
        self._render_head()

    # ------------------------------------------------------------------ others
    def _brick(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(label('🌍 블록 월드 맵' if self.name.lower().endswith('.bwr') else '🏠 블록 건축물 프리팹', title=True))
        v.addWidget(label('블록 데이터는 월드 에디터에서 3D로 보고 편집할 수 있습니다.', muted=True))
        v.addWidget(button('월드 에디터에서 열기', lambda: main_window().open_page('world', name=self.name), primary=True),
                    alignment=Qt.AlignLeft)
        v.addWidget(self._meta())
        v.addStretch(1)
        return w

    def _binary(self) -> QWidget:
        data = self.st.read(self.name)
        n = min(len(data), 4096)
        lines = []
        for i in range(0, n, 16):
            row = data[i:i + 16]
            lines.append(f'{i:08x}  {" ".join(f"{b:02x}" for b in row):<48}  '
                         + ''.join(chr(b) if 32 <= b < 127 else '.' for b in row))
        if len(data) > n:
            lines.append(f'… ({fmt_size(len(data))} 중 앞 4KB만 표시)')
        t = QPlainTextEdit('\n'.join(lines))
        t.setReadOnly(True)
        t.setFont(QFont('Consolas', 9))
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(t, 1)
        v.addWidget(label('파일을 끌어다 놓으면 그대로 교체됩니다', faint=True))
        v.addWidget(self._meta())
        return w

    # ------------------------------------------------------------------ actions
    def after_change(self):
        self.changed.emit(self.name)
        main_window().refresh_header()
        self.open(self.name)

    def _replace(self, path: str):
        if not main_window().require_project():
            return
        resize = kind_of(self.name) == 'image' and getattr(self, 'resize_box', None) is not None \
            and self.resize_box.isChecked()
        notes = R.replace_from_path(self.st, self.name, path, resize)
        msgs = ['교체했습니다: ' + os.path.basename(path)]
        if notes.get('converted'):
            msgs.append(f'형식 자동 변환 ({notes["converted"]})')
        if notes.get('resized_to'):
            msgs.append('원본 크기({}×{})로 맞춤'.format(*notes['resized_to']))
        if notes.get('size_changed'):
            sc = notes['size_changed']
            info('이미지 크기가 원본과 다릅니다: {}×{} → {}×{}\n게임에서 비율이 어긋날 수 있어요.'.format(*sc['from'], *sc['to']))
        toast(' · '.join(msgs))
        self.after_change()

    def replace_dialog(self):
        if not self.name:
            return
        p = open_file('교체할 파일', IMAGE_FILTER if kind_of(self.name) == 'image' else '모든 파일 (*.*)')
        if p:
            self._replace(p)

    def export_dialog(self):
        p = save_file('내보내기', os.path.basename(self.name.replace('\\', '/')))
        if p:
            R.export(self.st, self.name, p)
            toast('저장했습니다: ' + p)

    def open_external(self):
        if not main_window().require_project():
            return
        wp = R.external_open(self.st, self.name)
        opened = open_in_shell(wp)
        msg = ('외부 프로그램에서 파일을 열었습니다.' if opened else f'이 파일을 원하는 프로그램으로 편집하세요:\n{wp}') + \
            '\n\n편집하고 저장한 뒤 "적용"을 누르세요.'
        if confirm('외부 편집', msg, '편집한 파일 적용'):
            R.external_apply(self.st, self.name)
            toast('적용했습니다')
            self.after_change()

    def revert(self):
        if confirm('원본으로 되돌리기', f'{self.name}의 수정 내용을 지우고 게임 원본으로 되돌릴까요?', '되돌리기', danger=True):
            self.st.revert(self.name)
            toast('원본으로 되돌렸습니다')
            self.after_change()

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if self.name and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            guarded(self._replace)(urls[0])
