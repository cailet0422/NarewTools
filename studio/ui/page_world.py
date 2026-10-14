"""월드 에디터: edit .bwr worlds and .iaf prefabs in 3D."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QSlider, QSplitter, QTabWidget, QVBoxLayout, QWidget)

import os
import time

from .. import savedworlds, svc_blocks as B
from ..gl.voxels import BrickLUT
from ..svc_world import WorldService
from ..worldedit import unreadable_objects
from .common import (DARK, ask_text, button, confirm, debounce, error, guarded, hbox, info, label, pil_to_qimage,
                     run_task, save_file, toast)
from .pixelartdlg import ImageArtDialog
from .worldview import WorldView

TOOLS = [
    ('view', '👁 보기', '왼쪽 드래그: 회전, 오른쪽 드래그: 이동'),
    ('place', '➕ 놓기', '클릭한 면 앞에 선택한 블록을 놓습니다'),
    ('erase', '➖ 지우기', '클릭한 블록을 지웁니다'),
    ('paint', '🖌 칠하기', '클릭한 블록을 선택한 블록으로 바꿉니다'),
    ('pick', '💧 스포이드', '클릭한 블록을 팔레트에서 선택합니다'),
    ('box', '⬛ 상자', '두 번 클릭해 상자 영역을 채우거나 비웁니다'),
    ('prefab', '🏠 붙이기', '아래에서 고른 프리팹(.iaf)을 클릭한 곳에 붙입니다'),
    ('image', '🖼 이미지 도트', '그림을 블록 도트로 바꿔 클릭한 곳에 세웁니다 (그림·크기·방향은 아래 "🖼 이미지 고르기…")'),
    ('product', '🪑 설치물', '오른쪽 "설치물" 탭에서 고른 가구를 클릭한 곳에 놓습니다 · R: 90° 돌리기 (월드만)'),
    ('edit', '✋ 설치물 편집', '놓인 설치물을 클릭해 고른 뒤 R: 90° 돌리기, T: 조명 켜기/끄기, Delete: 지우기 (월드만)'),
    ('start', '🚩 시작 위치', '캐릭터가 처음 서는 위치 (월드만)'),
    ('object', '🌳 오브젝트 삭제', '나무·설치물 표시를 클릭해 지웁니다 (월드만)'),
]


class WorldPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.svc = WorldService(st)
        self.items = []
        self.saved = []               # the game's own world saves + captured copies (savedworlds)
        self.doc = None
        self.unreadable = None        # worldedit.unreadable_objects of the open world (furniture not read)
        self._warned = set()          # (world, game folder) already warned about it
        self.bricks = []
        self.atlas = None
        self.atlas_img = None         # the same atlas as a PIL image (colours for the image tool)
        self.image_settings = {}      # last choices of the image dialog (and its picture)
        # left
        self.search = QLineEdit()
        self.search.setPlaceholderText('맵·프리팹 검색')
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda it: it.data(Qt.UserRole) and self.open_doc(it.data(Qt.UserRole)))
        self._t = debounce(150, self.fill)
        self.search.textChanged.connect(lambda: self._t.start())
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(hbox(self.search, button('+ 새 프리팹', self.new_prefab)))
        ll.addWidget(hbox(button('📸 현재 월드 저장', self.capture_now, tip='게임이 저장한 지금 월드를 .bwr로 복사'),
                          button('캡처 창', self.mw.open_capture,
                                 tip='게임하면서 쓰는 작은 창 (단축키 Ctrl+Alt+S, 자동 저장)'), 'stretch'))
        ll.addWidget(hbox(button('🎮 내 월드에 적용', self.apply_to_world,
                                 tip='열어 둔 월드 파일로 게임의 내 월드(Local)를 바꿉니다. 지금 저장본은 먼저 백업합니다.'),
                          button('↩ 백업으로', self.restore_world, tip='내 월드를 백업해 둔 상태로 되돌립니다'), 'stretch'))
        ll.addWidget(self.list, 1)
        # center
        self.view = WorldView()
        self.view.hover_text.connect(lambda t: self.hud.setText(t))
        self.view.history_changed.connect(lambda u, r: (self.undo_btn.setEnabled(u), self.redo_btn.setEnabled(r)))
        self.view.picked.connect(self._picked)
        self.view.message.connect(lambda m: toast(m))
        self.view.camera_moved.connect(self._move_dot)
        self.view.selection_changed.connect(self._selection_changed)
        self.title = label('맵을 고르세요', title=True)
        self.undo_btn = button('↶ 되돌리기', self.view.undo, tip='Ctrl+Z')
        self.redo_btn = button('↷ 다시', self.view.redo, tip='Ctrl+Y')
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.save_btn = button('💾 저장', self.save, primary=True)
        self.tool_group = QButtonGroup(self)
        tool_btns = []
        for key, text, tip in TOOLS:
            b = button(text, tip=tip, checkable=True)
            b.setProperty('tool', key)
            self.tool_group.addButton(b)
            tool_btns.append(b)
        tool_btns[0].setChecked(True)
        self.tool_group.buttonToggled.connect(lambda b, on: on and self.set_tool(b.property('tool')))
        self.hud = label('마우스를 올리면 좌표와 블록 이름이 보입니다', muted=True)
        self.minimap = QLabel(self.view)
        self.minimap.setStyleSheet('border: 1px solid #313644; background: #000a;')
        self.minimap.hide()
        self.minimap.mousePressEvent = self._minimap_click
        self.dot = QLabel(self.minimap)
        self.dot.setFixedSize(8, 8)
        self.dot.setStyleSheet('background: #ffff44; border: 1px solid black; border-radius: 4px;')
        self.mm_scale = 1.0
        self.objects_box = QCheckBox('오브젝트 표시')
        self.objects_box.setChecked(True)
        self.objects_box.toggled.connect(lambda v: (setattr(self.view, 'show_objects', v), self.view.update()))
        self.coll_box = QCheckBox('충돌 블록(255) 표시')
        self.coll_box.toggled.connect(self.view.set_show_collision)
        self.radius = QSlider(Qt.Horizontal)
        self.radius.setRange(2, 16)
        self.radius.setValue(6)
        self.radius.setFixedWidth(120)
        self.radius.valueChanged.connect(self.view.set_radius)
        self.box_mode = QComboBox()
        self.box_mode.addItem('채우기', 'fill')
        self.box_mode.addItem('비우기', 'erase')
        self.box_mode.currentIndexChanged.connect(lambda: setattr(self.view, 'box_mode', self.box_mode.currentData()))
        self.prefab_box = QComboBox()
        self.prefab_box.setMinimumWidth(220)
        self.prefab_box.currentIndexChanged.connect(lambda: self.load_prefab(self.prefab_box.currentData()))
        self.skip_air = QCheckBox('빈칸은 덮지 않기')
        self.skip_air.setChecked(True)
        self.skip_air.toggled.connect(lambda v: setattr(self.view, 'skip_air', v))
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(hbox(self.title, 'stretch', self.undo_btn, self.redo_btn, self.save_btn, margins=(10, 6, 10, 6)))
        cl.addWidget(hbox(*tool_btns, 'stretch', margins=(10, 0, 10, 6)))
        cl.addWidget(self.view, 1)
        cl.addWidget(hbox(self.hud, margins=(10, 4, 10, 0)))
        cl.addWidget(hbox(self.objects_box, self.coll_box, label('보는 거리', muted=True), self.radius,
                          label('상자', muted=True), self.box_mode, label('프리팹', muted=True), self.prefab_box,
                          self.skip_air, button('🖼 이미지 고르기…', self.choose_image,
                                                tip='그림 파일을 블록 도트로 바꿉니다. 고른 뒤 맵을 클릭하면 세워집니다'),
                          'stretch', margins=(10, 4, 10, 6)))
        cl.addWidget(label('편집 도구에서는 오른쪽 드래그로 회전, 가운데 드래그로 이동, 휠로 확대, WASD/QE로 이동합니다.',
                           faint=True), alignment=Qt.AlignLeft)
        # right: palette
        self.pal_search = QLineEdit()
        self.pal_search.setPlaceholderText('블록 검색')
        self.pal_search.textChanged.connect(self.fill_palette)
        self.palette = QListWidget()
        self.palette.setIconSize(QSize(28, 28))
        self.palette.itemClicked.connect(lambda it: self._select_brick(it.data(Qt.UserRole)))
        self.sel_lbl = label('선택: -', muted=True)
        blocks_tab = QWidget()
        rl = QVBoxLayout(blocks_tab)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.addWidget(self.sel_lbl)
        rl.addWidget(self.pal_search)
        rl.addWidget(self.palette, 1)
        # right: products to place
        self.prod_search = QLineEdit()
        self.prod_search.setPlaceholderText('설치물 검색 (이름·번호)')
        self.prod_search.textChanged.connect(self.fill_products)
        self.prod_list = QListWidget()
        self.prod_list.itemClicked.connect(lambda it: self._select_product(it.data(Qt.UserRole)))
        self.prod_lbl = label('선택: -', muted=True, wrap=True)
        # a placed object picked with the 'edit' tool
        self.obj_lbl = label('"✋ 설치물 편집" 도구로 놓인 설치물을 클릭하면 돌리거나 켜고 끌 수 있습니다', muted=True, wrap=True)
        self.obj_rot_btn = button('↻ 돌리기 (R)', lambda: self.view.rotate_object(),
                                  tip='고른 설치물을 놓인 칸을 중심으로 90° 돌립니다 (충돌 칸도 함께)')
        self.obj_switch_btn = button('💡 켜기/끄기 (T)', lambda: self.view.toggle_object(),
                                     tip='조명처럼 켜고 끄는 설치물만. 꺼진 조명은 게임에서 빛나지 않습니다')
        self.obj_del_btn = button('🗑 지우기 (Del)', lambda: self.view.delete_selected(), danger=True)
        self._selection_changed(None)
        prod_tab = QWidget()
        pl = QVBoxLayout(prod_tab)
        pl.setContentsMargins(6, 6, 6, 6)
        pl.addWidget(label('놓인 설치물', title=True))
        pl.addWidget(self.obj_lbl)
        pl.addWidget(hbox(self.obj_rot_btn, self.obj_switch_btn, self.obj_del_btn, 'stretch'))
        pl.addWidget(label('새로 놓기', title=True))
        pl.addWidget(self.prod_lbl)
        pl.addWidget(hbox(button('↻ 돌리기 (R)', self.view.rotate_product), 'stretch'))
        pl.addWidget(self.prod_search)
        pl.addWidget(self.prod_list, 1)
        pl.addWidget(label('초록 상자: 놓을 수 있음, 빨강: 막힘. 놓으면 충돌 칸도 함께 채웁니다. 표지판·사진·상점·우편함 등 '
                           '자체 데이터가 필요한 종류(회색)는 아직 놓을 수 없습니다.', faint=True, wrap=True))
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(blocks_tab, '블록')
        self.right_tabs.addTab(prod_tab, '설치물')
        right = self.right_tabs
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setSizes([260, 960, 300])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    # ------------------------------------------------------------------ lists
    def on_show(self, name: str | None = None):
        if not self.st.game:
            return
        self.items = self.svc.list()
        self.load_saved()
        if name:
            self.open_doc(name)

    def load_saved(self):
        g = self.st.game.root if self.st.game else None
        self.saved = (savedworlds.list_saved(g) if g else []) + savedworlds.list_captured()
        self.fill()

    def on_state(self):
        if self.isVisible():
            self.on_show()

    def fill(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        head = QListWidgetItem('💾 내 월드 (게임 저장본·캡처)')
        head.setFlags(Qt.NoItemFlags)
        self.list.addItem(head)
        if not self.saved:
            li = QListWidgetItem('   (없음: 싱글 플레이로 들어가면 생깁니다)')
            li.setFlags(Qt.NoItemFlags)
            self.list.addItem(li)
        for s in self.saved:
            if q and q not in s['name'].lower():
                continue
            when = time.strftime('%m-%d %H:%M', time.localtime(s['mtime']))
            li = QListWidgetItem(f"   {s['name']}  · {s['label']} {when}")
            li.setToolTip(s['path'])
            li.setData(Qt.UserRole, ('file', s['path']))
            self.list.addItem(li)
        for t, title in (('world', '🌍 월드 맵 (.bwr)'), ('prefab', '🏠 프리팹 (.iaf)')):
            head = QListWidgetItem(title)
            head.setFlags(Qt.NoItemFlags)
            self.list.addItem(head)
            for it in self.items:
                if it['type'] != t or (q and q not in it['name'].lower()):
                    continue
                size = f"  {it['size'][0]}×{it['size'][2]}" if it['size'] else ''
                li = QListWidgetItem('   ' + it['name'].split('\\')[-1] + size + ('  ●' if it['status'] != 'original' else ''))
                li.setToolTip(it['name'])
                li.setData(Qt.UserRole, it['name'])
                self.list.addItem(li)
        self.prefab_box.blockSignals(True)
        cur = self.prefab_box.currentData()
        self.prefab_box.clear()
        self.prefab_box.addItem('(고르기)', None)
        for it in self.items:
            if it['type'] == 'prefab':
                self.prefab_box.addItem(it['name'].split('\\')[-1] + (' ' + '×'.join(map(str, it['size'])) if it['size'] else ''),
                                        it['name'])
        if cur:
            i = self.prefab_box.findData(cur)
            self.prefab_box.setCurrentIndex(max(0, i))
        self.prefab_box.blockSignals(False)

    def _tile_icon(self, t: int) -> QIcon:
        if t is None or t < 0 or self.atlas is None:
            return QIcon()
        s = self.atlas.width() // 16
        return QIcon(self.atlas.copy((t % 16) * s, (t // 16) * s, s, s))

    def fill_palette(self):
        q = self.pal_search.text().strip().lower()
        self.palette.clear()
        for b in self.bricks:
            text = f"#{b['id']} {b['label'] or b['name']}"
            if q and q not in (text + ' ' + b['category']).lower():
                continue
            it = QListWidgetItem(self._tile_icon(b['tiles'].get('top', -1)), text)
            it.setToolTip(f"{b['name']} · {b['category']}")
            it.setData(Qt.UserRole, b['id'])
            self.palette.addItem(it)

    def _select_brick(self, bid):
        self.view.selected = bid
        lut = self.view.lut
        self.sel_lbl.setText('선택: ' + (lut.label(bid) if lut else f'#{bid}'))

    def fill_products(self):
        self.prod_list.clear()
        if not self.doc or not self.doc.is_world:
            it = QListWidgetItem('월드(.bwr)를 열면 설치물을 놓을 수 있습니다')
            it.setFlags(Qt.NoItemFlags)
            self.prod_list.addItem(it)
            return
        q = self.prod_search.text().strip().lower()
        for p in self.doc.product_choices():
            if p['id'] == 0:                    # placeholder entry of product.xml
                continue
            text = f"#{p['id']}  {p['name']}  ({p['size'][0]}×{p['size'][1]}×{p['size'][2]})"
            if q and q not in text.lower():
                continue
            it = QListWidgetItem(text + ('  · 놓을 수 없음' if p['reason'] else ''))
            it.setData(Qt.UserRole, p['id'])
            it.setToolTip(p['reason'] or f"종류: {p['event']}")
            if p['reason']:
                it.setFlags(Qt.NoItemFlags)
                it.setForeground(QColor(DARK['faint']))
            self.prod_list.addItem(it)

    def _select_product(self, pid):
        if pid is None:
            return
        self.view.product = pid
        self.prod_lbl.setText('선택: ' + self.view.product_name(pid))
        for b in self.tool_group.buttons():
            if b.property('tool') == 'product':
                b.setChecked(True)
        self.view.setFocus()

    def _selection_changed(self, info):
        if info is None:
            self.obj_lbl.setText('"✋ 설치물 편집" 도구로 놓인 설치물을 클릭하면 돌리거나 켜고 끌 수 있습니다')
        else:
            self.obj_lbl.setText('고름: ' + info['text'])
        self.obj_rot_btn.setEnabled(bool(info and info['product']))
        self.obj_switch_btn.setEnabled(bool(info and info['switchable']))
        self.obj_switch_btn.setText('💡 끄기 (T)' if info and info['on'] else '💡 켜기 (T)' if info and info['switchable']
                                    else '💡 켜기/끄기 (T)')
        self.obj_del_btn.setEnabled(info is not None)

    def _picked(self, bid):
        self._select_brick(bid)
        toast('선택: ' + self.view.lut.label(bid))

    # ------------------------------------------------------------------ open / save
    def can_leave(self) -> bool:
        if self.view.dirty():
            return confirm('저장하지 않은 변경', '월드 편집 내용을 버릴까요?', '버리기', danger=True)
        return True

    def open_doc(self, name):
        if self.doc and name not in (self.doc.name, ('file', self.doc.path)) and not self.can_leave():
            return
        st = self.st

        def job():
            if isinstance(name, tuple):          # a world file: tables as the game played it (mods applied)
                doc = self.svc.open_file(name[1])
                return doc, self.svc.save_bricks(), self.svc.save_atlas()
            return self.svc.open(name), B.bricks(st), B.atlas_image(st)
        run_task(job, done=self._opened, busy='맵 여는 중')

    def reload_doc(self):
        """Open the current world/prefab again (an agent saved it), keeping the camera where it is."""
        if not self.doc:
            return
        cam = self.view.cam
        self._keep_cam = (cam.target.copy(), cam.dist, cam.yaw, cam.pitch)
        self.open_doc(('file', self.doc.path) if self.doc.path else self.doc.name)

    def _opened(self, r):
        doc, bricks, atlas = r
        self.doc, self.bricks = doc, bricks
        self.atlas_img = atlas
        self.atlas = QPixmap.fromImage(pil_to_qimage(atlas))
        self.view.open(doc, BrickLUT(bricks), atlas)
        self.unreadable = unreadable_objects(doc, self.st.game.root if self.st.game else None)
        keep, self._keep_cam = getattr(self, '_keep_cam', None), None
        if keep and self.view.ren is not None:
            cam = self.view.cam
            cam.target, cam.dist, cam.yaw, cam.pitch = keep
            self.view.camera_changed()
            self.view.update()
        self._set_title()
        self.fill_palette()
        self._select_brick(self.view.selected)
        self.fill_products()
        self._setup_minimap()
        self._warn_unreadable()

    def _warn_unreadable(self):
        """Once per world and game folder: some furniture of this world could not be read."""
        u = self.unreadable
        key = (self.doc.path or self.doc.name, self.st.game.root if self.st.game else None)
        if not u or key in self._warned:
            return
        self._warned.add(key)
        toast(f'⚠ 이 월드의 영역 {u["count"]}곳에서 가구·나무 {u["objects"]}개를 읽지 못했습니다 (제목의 ⚠ 참고)',
              15000)
        box = QMessageBox(QMessageBox.Warning, '가구 일부를 읽지 못했습니다', self._unreadable_text(),
                          QMessageBox.Ok, self)
        box.setModal(False)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()

    def _unreadable_text(self) -> str:
        return self.unreadable['hint'] + '\n\n위쪽의 게임 폴더 버튼으로 그 폴더를 고른 뒤 이 월드를 다시 여세요.'

    def _set_title(self):
        doc = self.doc
        size = f'({doc.width}×{doc.height}×{doc.depth})'
        if doc.path:
            g = self.st.game.root if self.st.game else None
            kind = '게임 저장본 — 원본은 그대로 두고 "저장"은 새 파일로' if savedworlds.is_game_save(g, doc.path) else '내 파일'
            self.title.setText(f'{os.path.basename(doc.path)}  {size}  · {kind}')
            self.title.setToolTip(doc.path)
        else:
            self.title.setText(f'{doc.name}  {size}  · {self.st.status_of(doc.name)}')
            self.title.setToolTip(doc.name)
        if self.unreadable:
            self.title.setText(self.title.text() + '  · ⚠ 가구 일부를 읽지 못함')
            self.title.setToolTip(self.title.toolTip() + '\n\n' + self._unreadable_text())

    def _setup_minimap(self):
        if not self.doc.is_world:
            self.minimap.hide()
            return
        doc = self.doc

        def done(path):
            pm = QPixmap(path)
            size = 200
            self.mm_scale = size / max(doc.width, doc.depth)
            self.minimap.setPixmap(pm.scaled(int(doc.width * self.mm_scale), int(doc.depth * self.mm_scale),
                                             Qt.IgnoreAspectRatio, Qt.FastTransformation))
            self.minimap.adjustSize()
            self.minimap.move(self.view.width() - self.minimap.width() - 10, 10)
            self.minimap.show()
            self.minimap.raise_()
            self._move_dot(self.view.cam.target[0], -self.view.cam.target[2])
        run_task(self.svc.topview, doc, done=done, busy='미니맵')

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.minimap.isVisible():
            self.minimap.move(self.view.width() - self.minimap.width() - 10, 10)

    def _move_dot(self, x, z):
        self.dot.move(int(x * self.mm_scale) - 4, int(z * self.mm_scale) - 4)

    def _minimap_click(self, e):
        p = e.position()
        self.view.jump_to(p.x() / self.mm_scale, p.y() / self.mm_scale)

    def set_tool(self, tool):
        if tool in ('start', 'object', 'product', 'edit') and self.doc and not self.doc.is_world:
            toast('월드(.bwr)에서만 쓸 수 있습니다')
        if tool in ('product', 'edit'):
            self.right_tabs.setCurrentIndex(1)
        if tool == 'product' and self.view.product is None:
            toast('오른쪽 "설치물" 탭에서 놓을 가구를 고르세요')
        if tool == 'edit':
            toast('놓인 설치물을 클릭해 고르세요 · R 돌리기, T 켜기/끄기, Delete 지우기')
        self.view.set_tool(tool)
        self.view.setFocus()
        if tool == 'prefab' and not self.view.prefab:
            toast('아래 "프리팹" 목록에서 붙일 건축물을 고르세요')
        if tool == 'image' and self.view.image_art is None and self.doc:
            self.choose_image()

    def choose_image(self):
        """Pick a picture and how to turn it into bricks, then switch to the image tool."""
        if not self.doc:
            toast('맵을 먼저 여세요')
            return
        dlg = ImageArtDialog(self, self.bricks, self.atlas_img, self._tile_icon, self.image_settings)
        if dlg.exec() != ImageArtDialog.Accepted:
            return
        self.image_settings = dlg.settings
        art = self.view.image_art = dlg.art
        for b in self.tool_group.buttons():
            if b.property('tool') == 'image':
                b.setChecked(True)
        sx, sy, sz = art.size
        toast(f'이미지 도트 {art.width}×{art.height} (블록 {art.blocks():,}개, 상자 {sx}×{sy}×{sz}): '
              '맵을 클릭하면 그 칸에 아래 가운데가 섭니다')
        self.view.setFocus()

    @guarded
    def load_prefab(self, name):
        if not name:
            self.view.prefab = None
            return
        d = self.svc.open(name)
        groups = {(rx, rz): d.region_array(rx, rz) for rz in range(d.rz) for rx in range(d.rx)}
        self.view.prefab = (d.width, d.height, d.depth, groups)
        for b in self.tool_group.buttons():
            if b.property('tool') == 'prefab':
                b.setChecked(True)

    @guarded
    def save(self):
        if not self.doc:
            return
        v = self.view
        if self.doc.path:                       # a world file outside the packs: never into the game's saves
            g = self.st.game.root if self.st.game else None
            path = self.doc.path
            if savedworlds.is_game_save(g, path):
                if not v.dirty():
                    toast('바뀐 내용이 없습니다. 원본 그대로 남기려면 "📸 현재 월드 저장"을 쓰세요.')
                    return
                stem = os.path.splitext(os.path.basename(path))[0]
                os.makedirs(savedworlds.default_capture_dir(), exist_ok=True)
                path = save_file('편집한 월드 저장 (.bwr)',
                                 os.path.join(savedworlds.default_capture_dir(), f'{stem}_edit.bwr'), '월드 (*.bwr)')
                if not path:
                    return
            elif not v.dirty():
                toast('바뀐 내용이 없습니다')
                return
            regions = {k: v.store.regions[k] for k in v.store.modified if k in v.store.regions}
            deleted, added, states, rots = v.object_edits()
            n = self.svc.save_as(self.doc, path, regions, deleted, v.start_pos if v.start_changed else None, added,
                                 states, rots)
            v.mark_saved()
            toast(f'저장했습니다: {path} ({n:,} 바이트)')
            self.load_saved()
            self._set_title()
            return
        if not self.mw.require_project():
            return
        if not v.dirty():
            toast('바뀐 내용이 없습니다')
            return
        regions = {k: v.store.regions[k] for k in v.store.modified if k in v.store.regions}
        deleted, added, states, rots = v.object_edits()
        n = self.svc.save(self.doc, regions, deleted, v.start_pos if v.start_changed else None, added, states, rots)
        v.mark_saved()
        toast(f'저장했습니다 (영역 {len(regions)}개, {n:,} 바이트)')
        self.mw.refresh_header()
        self.items = self.svc.list()
        self.fill()
        self._set_title()

    # ------------------------------------------------------------------ my world in the game
    @guarded
    def apply_to_world(self):
        if not self.mw.require_game():
            return
        g, doc = self.st.game.root, self.doc
        if not doc or not doc.is_world or not doc.path or savedworlds.is_game_save(g, doc.path):
            info('게임 저장본(💾 내 월드)을 열어 고친 뒤 "저장"으로 새 파일을 만들고, 그 파일을 연 상태에서 누르세요.\n'
                 '게임의 원본 저장본은 이 버튼으로만, 백업을 남기고 바뀝니다.')
            return
        if self.view.dirty():
            if not confirm('저장 먼저', '저장하지 않은 변경이 있습니다. 파일에 저장한 뒤 적용할까요?', '저장하고 적용'):
                return
            self.save()
            if self.view.dirty():
                return
        worlds = [w['name'] for w in savedworlds.local_worlds(g)]
        if not worlds:
            error('게임 폴더의 Local\\에 월드 저장본이 없습니다. 싱글 플레이로 한 번 들어갔다 나오세요.')
            return
        stem = os.path.splitext(os.path.basename(doc.path))[0].lower()
        guess = [w for w in worlds if stem.startswith(os.path.splitext(w)[0].lower())]
        world = guess[0] if guess else worlds[0] if len(worlds) == 1 else None
        if world is None:
            world, ok = QInputDialog.getItem(self, '내 월드에 적용', '바꿀 내 월드:', worlds, 0, False)
            if not ok:
                return
        chk = savedworlds.check_apply(g, doc.path, world)
        msg = (f"게임의 내 월드 {chk['world']}을(를) 이 파일로 바꿉니다:\n{doc.path}\n\n"
               f"지금 저장본(.bwr .lnd .npc .db)은 먼저 백업합니다:\n{savedworlds.backup_root()}\n\n"
               '게임을 완전히 끈 상태여야 합니다.')
        if chk['save_newer']:
            msg += ('\n\n⚠ 게임이 이 파일보다 나중에 저장했습니다 '
                    f"({time.strftime('%m-%d %H:%M', time.localtime(chk['save_time']))}). 적용하면 그 뒤 게임에서 "
                    '바뀐 것이 사라집니다 (백업에는 남습니다).')
        if not confirm('내 월드에 적용', msg, '적용', danger=True):
            return

        def done(r):
            self.load_saved()
            info(f"내 월드 {r['world']}에 적용했습니다.\n백업: {r['backup']}\n\n게임을 켜서 확인하세요. "
                 '되돌리려면 "↩ 백업으로"를 누르세요.', '적용 완료')
        run_task(savedworlds.apply_to_game, g, doc.path, world, done=done, busy='내 월드에 적용')

    @guarded
    def restore_world(self):
        if not self.mw.require_game():
            return
        backups = savedworlds.list_backups()
        if not backups:
            info('아직 백업이 없습니다. "🎮 내 월드에 적용"을 하면 그 전에 백업이 만들어집니다.')
            return
        rows = [f"{b['world']}  {time.strftime('%m-%d %H:%M:%S', time.localtime(b['time']))}  · {b.get('reason', '')}"
                for b in backups]
        choice, ok = QInputDialog.getItem(self, '백업으로 되돌리기', '되돌릴 백업 (지금 저장본도 먼저 백업합니다):', rows, 0, False)
        if not ok:
            return
        b = backups[rows.index(choice)]
        if not confirm('백업으로 되돌리기', f"내 월드 {b['world']}을(를) 이 백업으로 되돌릴까요?\n{b['dir']}", '되돌리기', danger=True):
            return
        run_task(savedworlds.restore_backup, self.st.game.root, b['dir'], busy='백업 되돌리기',
                 done=lambda r: (self.load_saved(), info(f"되돌렸습니다: {', '.join(r['restored'])}", '되돌리기 완료')))

    def capture_now(self):
        if not self.mw.require_game():
            return

        def done(r):
            toast(('저장했습니다: ' if r['new'] else '이미 저장한 상태입니다: ') + r['path'])
            self.load_saved()
        run_task(savedworlds.capture, self.st.game.root, done=done, busy='월드 저장')

    def new_prefab(self):
        if not self.mw.require_project():
            return
        name = ask_text('새 프리팹', '이름 (예: Architecture\\building\\my_house.iaf)', 'Architecture\\building\\my_house.iaf')
        if not name:
            return
        size = ask_text('새 프리팹', '크기 가로×높이×세로 (가로·세로는 16의 배수)', '32×32×32')
        if not size:
            return
        import re
        w, h, d = (int(x) for x in re.split(r'[x×*, ]+', size)[:3])
        n = self.svc.new_prefab(name, w, h, d)
        self.items = self.svc.list()
        self.fill()
        self.open_doc(n)
        toast('빈 프리팹을 만들었습니다. 놓기 도구로 바닥을 클릭해 블록을 놓으세요.')
