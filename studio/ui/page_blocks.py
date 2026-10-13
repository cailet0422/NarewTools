"""블록 텍스처: the 16x16 terrain atlas (diffuse + specular) and which bricks use each tile."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QGridLayout, QGroupBox, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QScrollArea, QSlider, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .. import imaging, svc_blocks as B
from .common import (DARK, IMAGE_FILTER, button, checker_pixmap, confirm, guarded, hbox, label, open_file,
                     pil_to_qimage, save_file, toast)

FACE_KO = {'top': '위', 'bottom': '아래', 'front': '앞', 'back': '뒤', 'left': '왼쪽', 'right': '오른쪽'}


class AtlasGrid(QWidget):
    tile_clicked = Signal(int)

    def __init__(self):
        super().__init__()
        self.atlas: QPixmap | None = None
        self.used: set[int] = set()
        self.changed: set[int] = set()
        self.selected = -1
        self.setMinimumSize(512, 512)
        self.setMouseTracking(True)
        self.hover = -1

    def set_data(self, atlas: QPixmap, used, changed):
        self.atlas, self.used, self.changed = atlas, set(used), set(changed)
        self.update()

    def cell(self) -> int:
        return max(16, min(self.width(), self.height()) // 16)

    def sizeHint(self):
        return QSize(640, 640)

    def paintEvent(self, e):
        p = QPainter(self)
        c = self.cell()
        p.fillRect(self.rect(), QColor(DARK['bg']))
        if not self.atlas:
            return
        t = self.atlas.width() // 16
        for i in range(256):
            x, y = (i % 16) * c, (i // 16) * c
            r = QRect(x + 1, y + 1, c - 2, c - 2)
            p.fillRect(r, QColor('#2a2d34'))
            p.drawPixmap(r, self.atlas, QRect((i % 16) * t, (i // 16) * t, t, t))
            if i not in self.used:
                p.fillRect(r, QColor(21, 23, 28, 150))
            if i in self.changed:
                p.setBrush(QColor(DARK['warn']))
                p.setPen(Qt.NoPen)
                p.drawEllipse(x + c - 10, y + 3, 7, 7)
            if i == self.selected or i == self.hover:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(DARK['accent'] if i == self.selected else '#ffffff'), 2))
                p.drawRect(r)
        p.end()

    def _index(self, pos):
        c = self.cell()
        x, y = int(pos.x()) // c, int(pos.y()) // c
        return y * 16 + x if 0 <= x < 16 and 0 <= y < 16 else -1

    def mouseMoveEvent(self, e):
        i = self._index(e.position())
        if i != self.hover:
            self.hover = i
            self.setToolTip(f'타일 {i}' if i >= 0 else '')
            self.update()

    def mousePressEvent(self, e):
        i = self._index(e.position())
        if i >= 0:
            self.selected = i
            self.update()
            self.tile_clicked.emit(i)


class BlocksPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.layer = 'diffuse'
        self.data = None
        self.selected = -1
        self.atlas_pm = None
        self.grid = AtlasGrid()
        self.grid.tile_clicked.connect(self.select)
        self.layer_box = QComboBox()
        self.layer_box.addItem('색상 (terrain.png)', 'diffuse')
        self.layer_box.addItem('반사광 (terrain_specular.png)', 'specular')
        self.layer_box.currentIndexChanged.connect(lambda: self._set_layer(self.layer_box.currentData()))
        self.brick_list = QListWidget()
        self.brick_list.setIconSize(QSize(36, 36))
        self.brick_filter = QLineEdit()
        self.brick_filter.setPlaceholderText('블록 이름 검색')
        self.brick_filter.textChanged.connect(self._fill_bricks)
        self.brick_list.itemClicked.connect(lambda it: self.select(it.data(Qt.UserRole)))
        tabs = QTabWidget()
        gw = QWidget()
        gl = QVBoxLayout(gw)
        gl.addWidget(label('흐린 칸은 어떤 블록도 쓰지 않는 타일입니다. 주황 점 = 수정됨.', muted=True))
        gl.addWidget(self.grid, 1)
        bw = QWidget()
        bl = QVBoxLayout(bw)
        bl.addWidget(self.brick_filter)
        bl.addWidget(self.brick_list, 1)
        tabs.addTab(gw, '타일')
        tabs.addTab(bw, '블록 목록')
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(hbox(label('레이어'), self.layer_box, 'stretch'))
        ll.addWidget(tabs, 1)
        self.detail = QWidget()
        self.dlay = QVBoxLayout(self.detail)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.detail)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(scroll)
        split.setSizes([700, 600])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.setAcceptDrops(True)

    def on_show(self, **kw):
        if not self.st.game:
            return
        self.reload()

    def on_state(self):
        if self.isVisible():
            self.reload()

    @guarded
    def reload(self):
        self.data = B.blocks_info(self.st)
        self.atlas_pm = QPixmap.fromImage(pil_to_qimage(B.atlas_image(self.st, self.layer)))
        used = set(self.data['usage'])
        self.grid.set_data(self.atlas_pm, used, self.data['changed'][self.layer])
        self._fill_bricks()
        if self.selected >= 0:
            self.select(self.selected)

    def _set_layer(self, layer):
        self.layer = layer
        self.reload()

    def _tile_pm(self, i: int, size: int) -> QPixmap:
        t = self.atlas_pm.width() // 16
        return self.atlas_pm.copy((i % 16) * t, (i // 16) * t, t, t).scaled(size, size, Qt.KeepAspectRatio,
                                                                          Qt.FastTransformation)

    def _fill_bricks(self):
        if not self.data:
            return
        q = self.brick_filter.text().strip().lower()
        self.brick_list.clear()
        for b in self.data['bricks']:
            text = f"#{b['id']}  {b['label'] or b['name']}   ({b['name']} · {b['category']})"
            if q and q not in text.lower():
                continue
            it = QListWidgetItem(text)
            t = b['tiles'].get('top', -1)
            if t >= 0:
                it.setIcon(QIcon(self._tile_pm(t, 36)))
            it.setData(Qt.UserRole, t)
            self.brick_list.addItem(it)

    def _clear_detail(self):
        while self.dlay.count():
            it = self.dlay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def select(self, i):
        if i is None or i < 0:
            return
        self.selected = i
        self.grid.selected = i
        self.grid.update()
        self._clear_detail()
        st = self.st
        changed = i in self.data['changed'][self.layer]
        self.dlay.addWidget(hbox(label(f'타일 {i}', title=True), label('수정됨' if changed else '원본', muted=True),
                                 label('색상 레이어' if self.layer == 'diffuse' else '반사광 레이어', faint=True), 'stretch'))
        cur = QLabel()
        cur.setPixmap(checker_pixmap(self._tile_pm(i, 256)))
        imgs = [hbox(label('현재', muted=True)), cur]
        if changed:
            org = QLabel()
            org.setPixmap(checker_pixmap(QPixmap.fromImage(pil_to_qimage(B.tile(st, i, self.layer, 'game'))).scaled(
                256, 256, Qt.KeepAspectRatio, Qt.FastTransformation)))
            imgs += [label('게임 원본', muted=True), org]
        row = QWidget()
        rg = QGridLayout(row)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.addWidget(imgs[0], 0, 0)
        rg.addWidget(imgs[1], 1, 0)
        if changed:
            rg.addWidget(imgs[2], 0, 1)
            rg.addWidget(imgs[3], 1, 1)
        rg.setColumnStretch(2, 1)
        self.dlay.addWidget(row)
        btns = [button('이미지로 교체…', self.replace, primary=True), button('내보내기…', self.export)]
        if changed:
            btns.append(button('이 타일 원본으로', self.reset, danger=True))
        self.dlay.addWidget(hbox(*btns, 'stretch'))
        self.dlay.addWidget(label('이미지를 끌어다 놓으면 이 타일이 교체됩니다 (크기는 자동으로 128×128에 맞춤)', faint=True))
        users = [b for b in self.data['bricks'] if i in b['tiles'].values()]
        self.dlay.addWidget(label('이 타일을 쓰는 블록', title=True))
        if users:
            for b in users:
                faces = '·'.join(FACE_KO[f] for f, t in b['tiles'].items() if t == i)
                self.dlay.addWidget(label(f"#{b['id']}  {b['label'] or b['name']}  —  {faces} 면", muted=True))
        else:
            self.dlay.addWidget(label('사용하는 블록 없음 (새 블록에 쓸 수 있는 빈 칸)', faint=True))
        self.dlay.addWidget(self._adjust_box(i))
        self.dlay.addWidget(label('블록 이름·단단함·면별 타일은 "새 리소스 추가"나 리소스 편집의 XML\\brick_table.xml에서 바꿉니다.',
                                  faint=True, wrap=True))
        self.dlay.addStretch(1)

    def _adjust_box(self, i) -> QWidget:
        box = QGroupBox('🎨 색 조정')
        g = QGridLayout(box)
        params = {'hue': 0.0, 'saturation': 1.0, 'brightness': 1.0, 'contrast': 1.0, 'tint': '#ff8040', 'tint_amount': 0.0}
        base = B.tile(self.st, i, self.layer)
        prev = QLabel()

        def refresh():
            out = imaging.adjust(base, **params)
            prev.setPixmap(checker_pixmap(QPixmap.fromImage(pil_to_qimage(out)).scaled(160, 160, Qt.KeepAspectRatio,
                                                                                       Qt.FastTransformation)))
        specs = [('색조', 'hue', -180, 180, 1), ('채도', 'saturation', 0, 200, 100), ('밝기', 'brightness', 0, 200, 100),
                 ('대비', 'contrast', 0, 200, 100), ('색 입히기', 'tint_amount', 0, 100, 100)]
        for r, (t, k, lo, hi, div) in enumerate(specs):
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(int(params[k] * div))
            s.valueChanged.connect(lambda v, k=k, div=div: (params.__setitem__(k, v / div), refresh()))
            g.addWidget(label(t), r, 0)
            g.addWidget(s, r, 1)
        cb = button('입힐 색…')

        def pick():
            c = QColorDialog.getColor(parent=self)
            if c.isValid():
                params['tint'] = c.name()
                refresh()
        cb.clicked.connect(pick)
        g.addWidget(cb, len(specs), 1)

        def apply():
            if not self.mw.require_project():
                return
            B.tile_adjust(self.st, i, self.layer, params)
            toast('색 조정을 적용했습니다')
            self._changed()
        g.addWidget(button('적용', apply, primary=True), len(specs) + 1, 1, alignment=Qt.AlignLeft)
        g.addWidget(prev, 0, 2, len(specs) + 2, 1)
        g.setColumnStretch(1, 1)
        refresh()
        return box

    def _changed(self):
        self.mw.refresh_header()
        self.reload()

    def replace(self, path: str | None = None):
        if self.selected < 0 or not self.mw.require_project():
            return
        path = path or open_file('타일 이미지', IMAGE_FILTER)
        if path:
            B.tile_from_path(self.st, self.selected, self.layer, path)
            toast('타일을 교체했습니다')
            self._changed()

    def export(self):
        p = save_file('타일 내보내기', f'tile_{self.selected}_{self.layer}.png', 'PNG (*.png)')
        if p:
            B.tile(self.st, self.selected, self.layer).save(p, 'PNG')
            toast('저장했습니다: ' + p)

    def reset(self):
        if confirm('타일 되돌리기', f'타일 {self.selected}을 게임 원본으로 되돌릴까요?', '되돌리기', danger=True):
            B.tile_reset(self.st, self.selected, self.layer)
            self._changed()

    def dragEnterEvent(self, e):
        if self.selected >= 0 and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            guarded(self.replace)(urls[0])
