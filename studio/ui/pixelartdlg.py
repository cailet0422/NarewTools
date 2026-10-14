"""이미지 도트: pick a picture, its size, direction and bricks for the world editor's pixel-art tool."""
from __future__ import annotations

import io

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QListWidget, QListWidgetItem, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import pixelart
from ..core import StudioError
from .common import DARK, button, debounce, error, hbox, label, open_file, pil_to_qimage, run_task

PREVIEW = 300
PRESET_ITEMS = (('plain', '무늬 적은 블록 (깔끔하게)'), ('all', '모든 블록 (색이 많지만 무늬가 섞임)'),
                ('custom', '직접 고르기'))


class ImageArtDialog(QDialog):
    """On accept self.art is a pixelart.PixelArt; self.settings keeps the choices (and the picture) for
    the next time the dialog opens."""

    def __init__(self, parent, bricks: list[dict], atlas: Image.Image, tile_icon, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle('이미지 도트')
        self.resize(820, 760)
        self.bricks, self.atlas, self.tile_icon = bricks, atlas, tile_icon
        self.settings = dict(settings or {})
        self.picture: Image.Image | None = self.settings.get('picture')
        self.art = None
        self._gen = 0
        self._cands: dict[bool, list[dict]] = {}
        self._t = debounce(250, self.recompute)

        self.file_lbl = label('그림을 고르세요 (PNG·JPG·BMP·GIF·WebP, 투명한 곳은 비워 둡니다)', muted=True, wrap=True)
        self.src_view, self.out_view = QLabel(), QLabel()
        for v in (self.src_view, self.out_view):
            v.setFixedSize(PREVIEW, PREVIEW)
            v.setAlignment(Qt.AlignCenter)
            v.setStyleSheet(f"background: {DARK['bg2']}; border: 1px solid {DARK['line']};")
        self.w_spin, self.h_spin = QSpinBox(), QSpinBox()
        for sp in (self.w_spin, self.h_spin):
            sp.setRange(1, pixelart.MAX_SIDE)
            sp.setSuffix(' 칸')
        self.w_spin.setValue(self.settings.get('w', 64))
        self.h_spin.setValue(self.settings.get('h', 64))
        self.keep = QCheckBox('비율 유지')
        self.keep.setChecked(self.settings.get('keep', True))
        self.facing = QComboBox()
        for k in pixelart.FACINGS:
            self.facing.addItem(pixelart.FACING_LABELS[k], k)
        self.facing.setCurrentIndex(max(0, self.facing.findData(self.settings.get('facing', '-z'))))
        self.double = QCheckBox('양면 (두께 2칸: 뒤에서 봐도 글자가 바로 읽힘)')
        self.double.setChecked(self.settings.get('double', True))
        self.sat = QSlider(Qt.Horizontal)
        self.sat.setRange(50, 200)
        self.sat.setValue(int(round(self.settings.get('sat', 1.3) * 100)))
        self.sat_lbl = label('', muted=True)
        self.dither = QCheckBox('디더링 (그러데이션이 부드러워지는 대신 표면이 자글자글해짐)')
        self.dither.setChecked(self.settings.get('dither', False))
        self.preset = QComboBox()
        for k, text in PRESET_ITEMS:
            self.preset.addItem(text, k)
        self.preset.setCurrentIndex(max(0, self.preset.findData(self.settings.get('preset', 'plain'))))
        self.brick_list = QListWidget()
        self.brick_list.setIconSize(QSize(22, 22))
        self.brick_list.setMinimumHeight(170)
        self.status = label('', muted=True, wrap=True)

        form = QFormLayout()
        form.addRow('크기', hbox(self.w_spin, label('×'), self.h_spin, self.keep, 'stretch'))
        form.addRow('방향', self.facing)
        form.addRow('', self.double)
        form.addRow('색 진하게', hbox(self.sat, self.sat_lbl))
        form.addRow('', self.dither)
        form.addRow('블록', hbox(self.preset, button('모두 선택', lambda: self._check_all(True)),
                                 button('모두 해제', lambda: self._check_all(False)), 'stretch'))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText('놓으러 가기')
        bb.button(QDialogButtonBox.Cancel).setText('취소')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(hbox(button('🖼 그림 파일…', self.pick_file), button('📋 클립보드에서', self.from_clipboard),
                           self.file_lbl, stretch_last=False))
        lay.addWidget(hbox(self._titled('그림', self.src_view), self._titled('블록으로', self.out_view), 'stretch'))
        lay.addLayout(form)
        lay.addWidget(self.brick_list, 1)
        lay.addWidget(self.status)
        lay.addWidget(label('놓으러 가기 → 맵에서 클릭한 칸에 그림의 아래 가운데가 섭니다(벽은 앞면, 바닥은 가운데). '
                            '노란 상자로 자리를 확인하세요. 되돌리기(Ctrl+Z) 한 번에 지워집니다.', faint=True, wrap=True))
        lay.addWidget(bb)

        self.w_spin.valueChanged.connect(lambda v: self._size_changed('w'))
        self.h_spin.valueChanged.connect(lambda v: self._size_changed('h'))
        self.facing.currentIndexChanged.connect(self._facing_changed)
        for sig in (self.double.toggled, self.dither.toggled):
            sig.connect(lambda *_: self._t.start())
        self.sat.valueChanged.connect(self._sat_changed)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.brick_list.itemChanged.connect(self._brick_toggled)
        self._sat_changed()
        self._facing_changed()
        if self.picture is not None:
            self._show_picture(self.settings.get('name', '이전 그림'))

    @staticmethod
    def _titled(text, w):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label(text, muted=True))
        lay.addWidget(w)
        return box

    # ------------------------------------------------------------------ picture
    def pick_file(self):
        path = open_file('그림 고르기', '그림 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tga);;모든 파일 (*.*)')
        if not path:
            return
        try:
            with Image.open(path) as im:
                self._set_picture(im.convert('RGBA'), path)
        except OSError as e:
            error(f'그림을 읽지 못했습니다: {path}\n{e}')

    def from_clipboard(self):
        qimg = QApplication.clipboard().image()
        if qimg.isNull():
            error('클립보드에 그림이 없습니다. 그림을 복사한 뒤 다시 누르세요.')
            return
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        qimg.save(buf, 'PNG')
        self._set_picture(Image.open(io.BytesIO(bytes(buf.data()))).convert('RGBA'), '클립보드 그림')

    def _set_picture(self, im: Image.Image, name: str):
        self.picture = im
        if 'w' not in self.settings:                 # first picture: small ones keep their own size
            self.w_spin.blockSignals(True)
            self.w_spin.setValue(min(64, im.width))
            self.w_spin.blockSignals(False)
        self.settings.update(picture=im, name=name)
        self._size_changed('w')
        self._show_picture(name)

    def _show_picture(self, name: str):
        im = self.picture
        self.file_lbl.setText(f'{name}  ({im.width}×{im.height})')
        pm = QPixmap.fromImage(pil_to_qimage(im))
        self.src_view.setPixmap(pm.scaled(PREVIEW, PREVIEW, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._t.start()

    # ------------------------------------------------------------------ settings
    def _size_changed(self, which: str):
        if self.keep.isChecked() and self.picture is not None:
            pw, ph = self.picture.width, self.picture.height
            if which == 'w':
                w, h = pixelart.fit_size(pw, ph, self.w_spin.value(), None)
            else:
                w, h = pixelart.fit_size(pw, ph, None, self.h_spin.value())
            for sp, v in ((self.w_spin, w), (self.h_spin, h)):
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        self._t.start()

    def _sat_changed(self):
        self.sat_lbl.setText(f'{self.sat.value()}%')
        self._t.start()

    def _facing_changed(self):
        floor = self.facing.currentData() == 'up'
        self.double.setEnabled(not floor)
        self._fill_bricks(self._checked_ids() if self.brick_list.count() else self.settings.get('ids'))
        self._t.start()

    def _cands_now(self) -> list[dict]:
        floor = self.facing.currentData() == 'up'
        if floor not in self._cands:
            self._cands[floor] = pixelart.candidates(self.bricks, self.atlas, ('top',) if floor else pixelart.SIDE_FACES)
        return self._cands[floor]

    def _fill_bricks(self, checked: list[int] | None):
        cands = self._cands_now()
        preset = self.preset.currentData()
        want = set(checked) if (preset == 'custom' and checked) else set(
            pixelart.preset_ids(cands, 'all' if preset == 'all' else 'plain'))
        by_id = {b['id']: b for b in self.bricks}
        face = 'top' if self.facing.currentData() == 'up' else 'front'
        self.brick_list.blockSignals(True)
        self.brick_list.clear()
        for c in sorted(cands, key=lambda c: c['id']):
            it = QListWidgetItem(self.tile_icon(by_id[c['id']]['tiles'].get(face, -1)),
                                 f"#{c['id']} {c['name']}" + ('  · 무늬 있음' if c['noise'] > pixelart.PLAIN_NOISE
                                                              and c['id'] not in pixelart.PLAIN_EXTRA else ''))
            it.setData(Qt.UserRole, c['id'])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c['id'] in want else Qt.Unchecked)
            self.brick_list.addItem(it)
        self.brick_list.blockSignals(False)

    def _checked_ids(self) -> list[int]:
        return [self.brick_list.item(i).data(Qt.UserRole) for i in range(self.brick_list.count())
                if self.brick_list.item(i).checkState() == Qt.Checked]

    def _preset_changed(self):
        if self.preset.currentData() != 'custom':
            self._fill_bricks(None)
        self._t.start()

    def _brick_toggled(self, _it):
        if self.preset.currentData() != 'custom':
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData('custom'))
            self.preset.blockSignals(False)
        self._t.start()

    def _check_all(self, on: bool):
        self.brick_list.blockSignals(True)
        for i in range(self.brick_list.count()):
            self.brick_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.brick_list.blockSignals(False)
        self._brick_toggled(None)

    # ------------------------------------------------------------------ result
    def _args(self):
        return (self.picture, self._cands_now(), self._checked_ids(), self.w_spin.value(), self.h_spin.value(),
                self.facing.currentData(), self.double.isChecked(), self.sat.value() / 100, self.dither.isChecked())

    def recompute(self):
        if self.picture is None:
            self.status.setText('그림을 먼저 고르세요.')
            return
        if not self._checked_ids():
            self.status.setText('블록을 하나 이상 고르세요.')
            return
        self._gen += 1
        gen = self._gen
        self.status.setText('바꾸는 중…')
        run_task(pixelart.make, *self._args(), done=lambda art: self._show_art(gen, art),
                 failed=lambda msg: gen == self._gen and self.status.setText(msg))

    def _show_art(self, gen: int, art):
        if gen != self._gen:
            return
        self.art = art
        pm = QPixmap.fromImage(pil_to_qimage(Image.fromarray(art.preview, 'RGBA')))
        k = max(1, PREVIEW // max(art.width, art.height))
        self.out_view.setPixmap(pm.scaled(art.width * k, art.height * k, Qt.KeepAspectRatio, Qt.FastTransformation))
        sx, sy, sz = art.size
        used = art.counts()
        top = ', '.join(f'{n} {c:,}' for _, n, c in used[:4])
        self.status.setText(f'{art.width}×{art.height}칸 · 블록 {art.blocks():,}개 · {len(used)}종 · 상자 '
                            f'{sx}×{sy}×{sz} (x×y×z)\n많이 쓴 블록: {top}')

    def accept(self):
        if self.picture is None:
            error('그림을 먼저 고르세요.')
            return
        if not self._checked_ids():
            error('블록을 하나 이상 고르세요.')
            return
        try:
            self.art = pixelart.make(*self._args())          # exactly what the controls say now
        except StudioError as e:
            error(str(e))
            return
        self.settings.update(w=self.w_spin.value(), h=self.h_spin.value(), keep=self.keep.isChecked(),
                             facing=self.facing.currentData(), double=self.double.isChecked(),
                             sat=self.sat.value() / 100, dither=self.dither.isChecked(),
                             preset=self.preset.currentData(), ids=self._checked_ids())
        super().accept()
