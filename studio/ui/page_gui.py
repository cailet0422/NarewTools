"""GUI 편집: draw a Gui\\*.xml layout with its gui_skin images, select nodes, drag them to move."""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGraphicsItem, QGraphicsObject, QGraphicsScene, QGraphicsView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QSpinBox, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from narew.modfmt import entry_key

from .. import guilayout as G, tables, textcodec
from .codeedit import CodePanel
from .common import (DARK, IMAGE_FILTER, button, checker_pixmap, confirm, debounce, guarded, hbox, label, open_file,
                     pixmap_from_bytes, scaled, toast)

RESOLUTIONS = [(1920, 1080), (1600, 900), (1280, 720)]
TEXT_TYPES = ('extended_text', 'text', 'button', 'editbox')
H_ALIGN = {'left': Qt.AlignLeft, 'center': Qt.AlignHCenter, 'right': Qt.AlignRight}
V_ALIGN = {'top': Qt.AlignTop, 'center': Qt.AlignVCenter, 'bottom': Qt.AlignBottom}


class NodeItem(QGraphicsObject):
    """One layout node drawn at its absolute rectangle; dragging reports the move on release."""

    def __init__(self, page: 'GuiPage', box: G.Box, index: int):
        super().__init__()
        self.page, self.box, self.index = page, box, index
        self.w, self.h = max(1.0, box.w), max(1.0, box.h)
        self.setPos(box.x, box.y)
        self.setZValue(index)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        if not box.visible:
            self.setOpacity(0.35)
        self._origin = None

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def paint(self, p, option, widget=None):
        self.page.paint_node(p, self)

    def mousePressEvent(self, e):
        self._origin = self.pos()
        self.page.select(self.index, from_scene=True)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self._origin is not None:
            d = self.pos() - self._origin
            self._origin = None
            if round(d.x()) or round(d.y()):
                # the move redraws the scene, which deletes this item: not inside its own event
                idx, dx, dy = self.page.selected_indices() or [self.index], round(d.x()), round(d.y())
                QTimer.singleShot(0, lambda: self.page.move_nodes(idx, dx, dy))


class GuiView(QGraphicsView):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)          # drag on empty space: box-select
        self.setRubberBandSelectionMode(Qt.ContainsItemShape)
        self.setBackgroundBrush(QColor('#0d0f13'))

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def keyPressEvent(self, e):
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
        d = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0), Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}.get(e.key())
        if d and self.page.sel is not None:
            self.page.move_nodes(self.page.selected_indices() or [self.page.sel], *d)
            return
        if e.key() == Qt.Key_Delete:
            self.page.delete_selected()
            return
        super().keyPressEvent(e)


class ImagePicker(QDialog):
    """Pick a gui_skin image (or bring in a PNG of your own) for a new element."""

    def __init__(self, page: 'GuiPage'):
        super().__init__(page)
        self.page = page
        self.setWindowTitle('그림 고르기')
        self.resize(640, 520)
        self.result_path = None
        self.search = QLineEdit()
        self.search.setPlaceholderText('그림 검색 (예: btn, frm, icon)')
        self.list = QListWidget()
        self.preview = QLabel()
        self.preview.setFixedSize(220, 220)
        self.preview.setAlignment(Qt.AlignCenter)
        self.info = label('', muted=True, wrap=True)
        self.names = page.skin_images()
        self.search.textChanged.connect(self.fill)
        self.list.currentItemChanged.connect(lambda it, _: it and self.show_image(it.data(Qt.UserRole)))
        self.list.itemDoubleClicked.connect(lambda it: self.accept())
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(self.preview)
        sl.addWidget(self.info)
        sl.addWidget(button('내 그림 파일 가져오기…', self.import_file,
                            tip='PNG 등을 모드의 gui_skin\\mod\\에 넣고 그 그림을 씁니다'))
        sl.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.search)
        lay.addWidget(hbox(self.list, side))
        lay.addWidget(bb)
        self.fill()

    def fill(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for rel in self.names:
            if not q or q in rel.lower():
                it = QListWidgetItem(rel)
                it.setData(Qt.UserRole, rel)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def show_image(self, rel):
        pm = self.page.pixmap(rel)
        self.preview.setPixmap(checker_pixmap(scaled(pm, 220, 220, pixel=True)) if pm else QPixmap())
        self.info.setText(f'{rel}\n{pm.width()}×{pm.height()}' if pm else rel)

    @guarded
    def import_file(self):
        if not self.page.mw.require_project():
            return
        src = open_file('그림 파일', IMAGE_FILTER)
        if not src:
            return
        from .. import imaging
        stem = os.path.splitext(os.path.basename(src))[0]
        safe = ''.join(c if c.isascii() and (c.isalnum() or c in '_-') else '_' for c in stem) or 'image'
        rel = f'mod\\{safe}.png'
        with open(src, 'rb') as f:
            im = imaging.open_image(f.read())
        self.page.st.write(G.texture_entry(rel), imaging.encode_like(im, '.png'))
        self.page.forget_images()
        self.names = self.page.skin_images()
        self.search.setText(safe)
        self.result_path = rel

    def accept(self):
        it = self.list.currentItem()
        if it is not None:
            self.result_path = it.data(Qt.UserRole)
        super().accept()


class GuiPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.name = None
        self.text, self.info, self.saved_text = '', None, ''
        self.root = None
        self.boxes: list[G.Box] = []
        self.items: list[NodeItem] = []
        self.sel = None
        self.undo_stack, self.redo_stack = [], []
        self._pix, self._fonts = {}, {}
        self._names = None
        # left: layouts
        self.search = QLineEdit()
        self.search.setPlaceholderText('화면 검색 (예: inventory)')
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda it: self.open_layout(it.data(Qt.UserRole)))
        self._t = debounce(150, self.fill)
        self.search.textChanged.connect(lambda: self._t.start())
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(self.search)
        ll.addWidget(self.list, 1)
        ll.addWidget(label('gui.pack의 Gui\\*.xml — 화면마다 하나. 그림은 gui_skin.pack에서 읽습니다.', faint=True, wrap=True))
        # center: canvas
        self.title = label('화면을 고르세요', title=True)
        self.res = QComboBox()
        for w, h in RESOLUTIONS:
            self.res.addItem(f'{w}×{h}', (w, h))
        self.res.currentIndexChanged.connect(lambda: self.render(keep_view=False))
        self.hidden = QCheckBox('숨은 요소도 보기')
        self.hidden.toggled.connect(lambda: self.render())
        self.outline = QCheckBox('윤곽선')
        self.outline.setChecked(True)
        self.outline.toggled.connect(lambda: self.scene.update())
        self.undo_btn = button('↶ 되돌리기', self.undo, tip='Ctrl+Z')
        self.redo_btn = button('↷ 다시', self.redo, tip='Ctrl+Y')
        self.save_btn = button('💾 모드에 저장', self.save, primary=True)
        self.scene = QGraphicsScene(self)
        self.view = GuiView(self)
        self.view.setScene(self.scene)
        self.hud = label('클릭: 고르기 · Ctrl+클릭/빈 곳 끌기: 여러 개 고르기 · 끌기/방향키(Shift 10px): 옮기기 · 휠: 확대', muted=True)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(hbox(self.title, 'stretch', self.undo_btn, self.redo_btn,
                          button('원본으로', self.revert, danger=True, tip='이 화면의 모드 수정본을 지웁니다'),
                          self.save_btn, margins=(10, 6, 10, 6)))
        cl.addWidget(hbox(label('화면 크기', muted=True), self.res, self.hidden, self.outline,
                          button('화면 맞춤', self.fit), 'stretch',
                          button('＋ 이미지', lambda: self.add_element('image'), tip='선택한 창 안에 그림 요소를 더합니다'),
                          button('＋ 버튼', lambda: self.add_element('button'),
                                 tip='모양만 있는 버튼 — 게임 코드가 모르는 버튼이라 눌러도 동작은 없습니다'),
                          button('＋ 글자', lambda: self.add_element('extended_text'), tip='글자 요소를 더합니다'),
                          button('🗑 지우기', self.delete_selected, danger=True, tip='선택한 요소 지우기 (Delete)'),
                          margins=(10, 0, 10, 6)))
        cl.addWidget(self.view, 1)
        cl.addWidget(hbox(self.hud, margins=(10, 4, 10, 6)))
        # right: tree, properties, xml
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemClicked.connect(lambda it: self.select(it.data(0, Qt.UserRole)))
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        props = QWidget()
        pf = QFormLayout(props)
        self.px, self.py, self.sw, self.sh = (QSpinBox() for _ in range(4))
        for s in (self.px, self.py):
            s.setRange(-10000, 10000)
        for s in (self.sw, self.sh):
            s.setRange(0, 10000)
        for s in (self.px, self.py, self.sw, self.sh):
            s.setKeyboardTracking(False)
            s.valueChanged.connect(self._props_changed)
        self.align_lbl = label('-', muted=True)
        self.tex_lbl = label('-', selectable=True, wrap=True)
        self.tex_btn = button('그림 편집', self.edit_texture, tip='리소스 편집에서 이 그림을 바꿉니다')
        self.txt = QLineEdit()
        self.txt.setPlaceholderText('글자 요소를 고르면 바꿀 수 있습니다')
        self.txt.editingFinished.connect(self._text_changed)
        self.fsize = QSpinBox()
        self.fsize.setRange(4, 200)
        self.fsize.setKeyboardTracking(False)
        self.fsize.valueChanged.connect(self._font_changed)
        self.color_btn = button('색…', self.pick_color)
        pf.addRow('위치 x / y', hbox(self.px, self.py))
        pf.addRow('크기 w / h', hbox(self.sw, self.sh))
        pf.addRow('정렬', self.align_lbl)
        pf.addRow('그림', self.tex_lbl)
        pf.addRow('', self.tex_btn)
        pf.addRow('글자', self.txt)
        pf.addRow('글자 크기 / 색', hbox(self.fsize, self.color_btn, 'stretch'))
        self.code = CodePanel()
        self.code.editor.set_language('xml')
        xml = QWidget()
        xl = QVBoxLayout(xml)
        xl.setContentsMargins(0, 0, 0, 0)
        xl.addWidget(self.code, 1)
        xl.addWidget(hbox(button('텍스트 적용', self.apply_text, tip='아래 XML을 고친 뒤 눌러 화면에 반영'), 'stretch',
                          margins=(6, 2, 6, 4)))
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.tree)
        right.addWidget(props)
        right.addWidget(xml)
        right.setSizes([330, 250, 330])
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setSizes([230, 900, 380])
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
            self.open_layout(name)

    def on_state(self):
        self._pix.clear()
        self._names = None
        if self.isVisible():
            self.fill()

    def layouts(self) -> list[str]:
        names = {k: loc.entry.name for k, loc in self.st.game.index.items()
                 if k.startswith('gui\\') and k.endswith('.xml') and k != 'gui\\sound.xml'}
        for k, (n, _) in self.st.project_files().items():
            if k.startswith('gui\\') and k.endswith('.xml') and k != 'gui\\sound.xml':
                names[k] = n
        return [names[k] for k in sorted(names)]

    def fill(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for n in self.layouts():
            if q and q not in n.lower():
                continue
            mod = self.st.status_of(n) != 'original'
            it = QListWidgetItem(n.split('\\')[-1] + ('  ●' if mod else ''))
            it.setToolTip(n + ('\n(모드에서 수정함)' if mod else ''))
            it.setData(Qt.UserRole, n)
            self.list.addItem(it)

    # ------------------------------------------------------------------ open / edit
    def can_leave(self) -> bool:
        if self.name and self.text != self.saved_text:
            return confirm('저장하지 않은 변경', f'{self.name}의 변경을 버릴까요?', '버리기', danger=True)
        return True

    @guarded
    def open_layout(self, name: str):
        if self.name and name != self.name and not self.can_leave():
            return
        text, info = textcodec.decode(self.st.read(name))
        self.name, self.text, self.info, self.saved_text = name, text, info, text
        self.undo_stack, self.redo_stack = [], []
        self.sel = None
        self.title.setText(f'{name}  · {self.st.status_of(name)}')
        self._reparse()
        self.render(keep_view=False)
        self._load_code()
        self._update_buttons()

    def _reparse(self):
        self.root = G.parse(self.text)

    def set_text(self, new: str):
        if new == self.text:
            return
        self.undo_stack.append(self.text)
        self.redo_stack.clear()
        self.text = new
        self._reparse()
        self.render()
        self._load_code()
        self._update_buttons()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.text)
            self.text = self.undo_stack.pop()
            self._reparse()
            self.render()
            self._load_code()
            self._update_buttons()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.text)
            self.text = self.redo_stack.pop()
            self._reparse()
            self.render()
            self._load_code()
            self._update_buttons()

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier and e.key() in (Qt.Key_Z, Qt.Key_Y):
            (self.undo if e.key() == Qt.Key_Z else self.redo)()
            return
        super().keyPressEvent(e)

    def _update_buttons(self):
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.redo_btn.setEnabled(bool(self.redo_stack))
        self.save_btn.setEnabled(bool(self.name) and self.text != self.saved_text)

    def _load_code(self):
        bar = self.code.editor.verticalScrollBar().value()
        self.code.editor.setPlainText(self.text)
        self.code.editor.verticalScrollBar().setValue(bar)

    @guarded
    def apply_text(self):
        self.set_text(self.code.editor.toPlainText())

    def move_node(self, index: int, dx: int, dy: int):
        self.move_nodes([index], dx, dy)

    def selected_indices(self) -> list[int]:
        """Selected nodes, without those inside another selected node (they move with it)."""
        idx = sorted(it.index for it in self.items if it is not None and it.isSelected())
        nodes = G.all_nodes(self.root) if self.root is not None else []
        return [i for i in idx if not any(j != i and G.is_ancestor(nodes[j], nodes[i]) for j in idx)]

    def move_nodes(self, indices: list[int], dx: int, dy: int):
        """Move nodes by (dx, dy) as one undo step."""
        text = self.text
        for i in indices:
            n = G.all_nodes(G.parse(text))[i]
            x, y = G.node_pos(n)
            text = G.set_pos(text, n, x + dx, y + dy)
        self.keep_selected = list(indices)
        if self.sel is None or self.sel not in indices:
            self.sel = indices[0] if indices else None
        self.set_text(text)

    def _parent_for_new(self):
        """The container a new element goes into: the selected node or its nearest ancestor that
        groups elements (a dialog/unfold/dragview, or an image that already has child elements)."""
        def groups(n):
            if n.tag != 'node' or n.inner_end < 0:
                return False
            t = n.attrs.get('type', '').strip()
            return t in ('dialog', 'unfold', 'dragview') or (t == 'image' and bool(n.nodes()))
        nodes = G.all_nodes(self.root)
        n = nodes[self.sel] if self.sel is not None else None
        while n is not None and not groups(n):
            n = n.parent
        if n is None or n.tag != 'node':
            n = next((x for x in nodes if x.inner_end >= 0), None)
        return n

    def skin_images(self) -> list[str]:
        names = {k: loc.entry.name for k, loc in self.st.game.index.items()
                 if k.startswith('gui_skin\\') and k.endswith(('.png', '.jpg', '.tga', '.dds'))}
        for k, (n, _) in self.st.project_files().items():
            if k.startswith('gui_skin\\') and k.endswith(('.png', '.jpg', '.tga', '.dds')):
                names[k] = n
        return [names[k].split('\\', 1)[1] for k in sorted(names)]

    def forget_images(self):
        self._pix.clear()
        self._names = None

    @guarded
    def add_element(self, kind: str):
        if self.root is None:
            toast('먼저 화면을 고르세요')
            return
        parent = self._parent_for_new()
        if parent is None:
            toast('요소를 넣을 창이 없습니다')
            return
        texture, w, h = None, 160, 40
        if kind in ('image', 'button'):
            dlg = ImagePicker(self)
            if not dlg.exec() or not dlg.result_path:
                return
            texture = dlg.result_path
            pm = self.pixmap(texture)
            w, h = (pm.width(), pm.height()) if pm else (64, 64)
        lines = G.new_node(kind, 20, 20, w, h, texture, '새 글자' if kind == 'extended_text' else None)
        pi = G.node_index(self.root, parent)
        new_text = G.insert_node(self.text, parent, lines)
        r = G.parse(new_text)
        nodes = G.all_nodes(r)
        kids = [n for n in nodes if n.parent is nodes[pi]]
        self.sel = nodes.index(kids[-1]) if kids else pi
        self.keep_selected = [self.sel]
        self.set_text(new_text)
        toast('요소를 더했습니다. 끌어서 옮기세요.' + (' (새 버튼은 눌러도 게임 기능이 없습니다)' if kind == 'button' else ''))

    @guarded
    def delete_selected(self):
        idx = self.selected_indices() or ([self.sel] if self.sel is not None else [])
        if not idx:
            return
        nodes = G.all_nodes(self.root)
        used = [i for i in idx if nodes[i].attrs.get('id') or nodes[i].child('event') is not None]
        msg = f'선택한 요소 {len(idx)}개를 지울까요?'
        if used:
            msg += ('\n\n⚠ 그중 {}개는 게임 코드가 쓰는 요소(id나 이벤트가 있음)라 지우면 그 창이 이상하게 '
                    '동작할 수 있습니다. 숨기려면 지우는 대신 크기를 0으로 하는 편이 안전합니다.').format(len(used))
        if not confirm('요소 지우기', msg, '지우기', danger=True):
            return
        text = self.text
        for i in sorted(idx, reverse=True):                    # later nodes first: earlier offsets stay valid
            text = G.remove_node(text, G.all_nodes(G.parse(text))[i])
        self.sel = None
        self.keep_selected = []
        self.set_text(text)

    def _text_changed(self):
        if self.sel is None or self._filling_props:
            return
        n = G.all_nodes(self.root)[self.sel]
        if self.txt.text() != (G.node_text(n) or ''):
            self.set_text(G.set_text(self.text, n, self.txt.text()))

    def _font_changed(self):
        if self.sel is None or self._filling_props:
            return
        n = G.all_nodes(self.root)[self.sel]
        self.set_text(G.set_font_size(self.text, n, self.fsize.value()))

    def pick_color(self):
        if self.sel is None:
            return
        n = G.all_nodes(self.root)[self.sel]
        cur = G.argb(n.child('color').attrs.get('font') if n.child('color') is not None else None) or (255, 255, 255, 255)
        c = QColorDialog.getColor(QColor(cur[0], cur[1], cur[2], cur[3]), self, '글자 색', QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self.set_text(G.set_text_color(self.text, n, (c.red(), c.green(), c.blue(), c.alpha())))

    def _props_changed(self):
        if self.sel is None or self._filling_props:
            return
        n = G.all_nodes(self.root)[self.sel]
        b = self.boxes[self.sel]
        text = self.text
        if (self.px.value(), self.py.value()) != tuple(int(v) for v in G.node_pos(n)):
            text = G.set_pos(text, n, self.px.value(), self.py.value())
        if (self.sw.value(), self.sh.value()) != (int(b.w), int(b.h)):
            n2 = G.all_nodes(G.parse(text))[self.sel]
            text = G.set_size(text, n2, self.sw.value(), self.sh.value())
        self.set_text(text)

    _filling_props = False

    # ------------------------------------------------------------------ save
    @guarded
    def save(self):
        if not self.name or not self.mw.require_project():
            return
        self.st.write(self.name, textcodec.encode(self.text, self.info, errors='xmlcharrefreplace'))
        self.saved_text = self.text
        self.title.setText(f'{self.name}  · {self.st.status_of(self.name)}')
        self.mw.refresh_header()
        self.fill()
        self._update_buttons()
        toast(f'모드에 저장했습니다: {self.name}')

    @guarded
    def revert(self):
        if not self.name or not self.st.project or self.st.status_of(self.name) == 'original':
            toast('모드에서 바꾼 적이 없는 화면입니다')
            return
        if confirm('원본으로', f'{self.name}의 모드 수정본을 지우고 원본으로 돌릴까요?', '원본으로', danger=True):
            self.st.revert(self.name)
            self.saved_text = self.text          # nothing left to keep
            name, self.name = self.name, None
            self.mw.refresh_header()
            self.open_layout(name)
            self.fill()

    def edit_texture(self):
        if self.sel is None:
            return
        tex = G.all_nodes(self.root)[self.sel].child('texture')
        if tex is not None and tex.text:
            entry = self.texture_name(tex.text)
            if entry:
                self.mw.open_page('resources', name=entry)

    # ------------------------------------------------------------------ drawing
    def texture_name(self, path: str) -> str | None:
        entry = G.texture_entry(path)
        if self.st.exists(entry):
            return self.st.canonical_name(entry)
        if self._names is None:
            names = [loc.entry.name for loc in self.st.game.index.values()]
            names += [n for n, _ in self.st.project_files().values()]
            self._names = tables.BasenameIndex(names)
        return self._names.find(path, prefer_top=G.SKIN_DIR)

    def pixmap(self, path: str):
        key = entry_key(path.strip())
        if key not in self._pix:
            name = self.texture_name(path)
            pm = pixmap_from_bytes(self.st.read(name)) if name else None
            self._pix[key] = pm if pm is not None and not pm.isNull() else None
        return self._pix[key]

    def font_family(self, file: str | None) -> str:
        file = (file or '').strip()
        if file not in self._fonts:
            fam = 'Malgun Gothic'
            name = self.texture_name(file) if file else None
            if name:
                fid = QFontDatabase.addApplicationFontFromData(self.st.read(name))
                fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                fam = fams[0] if fams else fam
            self._fonts[file] = fam
        return self._fonts[file]

    def render(self, keep_view: bool = True):
        if self.root is None:
            return
        w, h = self.res.currentData()
        self.scene.clear()
        self.items = []
        self.scene.setSceneRect(QRectF(-40, -40, w + 80, h + 80))
        frame = self.scene.addRect(QRectF(0, 0, w, h), QPen(QColor('#3a4152'), 2), QColor('#1b1f27'))
        frame.setZValue(-1)

        def size_of(path):
            pm = self.pixmap(path) if path else None
            return (pm.width(), pm.height()) if pm else None
        self.boxes = G.layout(self.root, w, h, size_of)
        show_hidden = self.hidden.isChecked()
        for i, b in enumerate(self.boxes):
            if not b.visible and not show_hidden:
                self.items.append(None)
                continue
            it = NodeItem(self, b, i)
            self.scene.addItem(it)
            self.items.append(it)
        self._fill_tree()
        keep = [i for i in getattr(self, 'keep_selected', []) if i < len(self.items)]
        if self.sel is not None and self.sel < len(self.items):
            self.select(self.sel)
        for i in keep:
            if self.items[i] is not None:
                self.items[i].setSelected(True)
        if not keep_view:
            self.fit(whole_screen=False)

    def fit(self, whole_screen: bool | None = None):
        """Zoom to the drawn window (default after opening) or, every other press, the whole screen."""
        w, h = self.res.currentData()
        if whole_screen is None:
            whole_screen = getattr(self, '_fit_window', False)
        rect = QRectF()
        if not whole_screen:
            for it in self.items:
                if it is not None and it.box.visible and it.box.depth <= 1:
                    rect = rect.united(it.sceneBoundingRect())
        if rect.isEmpty():
            rect = QRectF(0, 0, w, h)
        self._fit_window = not whole_screen
        self.view.fitInView(rect.adjusted(-30, -30, 30, 30), Qt.KeepAspectRatio)

    def paint_node(self, p: QPainter, item: NodeItem):
        n = item.box.node
        rect = QRectF(0, 0, item.w, item.h)
        tex = n.child('texture')
        if tex is not None and tex.text:
            pm = self.pixmap(tex.text)
            if pm is not None:
                ss = n.child('skinset')
                if ss is not None:
                    self._nine_slice(p, pm, rect, ss.attrs)
                elif tex.attrs.get('uv', '').strip() == 'wrap':
                    p.drawTiledPixmap(rect, pm)
                else:
                    p.drawPixmap(rect, pm, QRectF(pm.rect()))
            else:
                p.setPen(QPen(QColor(DARK['bad']), 1, Qt.DashLine))
                p.drawRect(rect)
        txt = n.child('text')
        if n.attrs.get('type', '').strip() in TEXT_TYPES and txt is not None and txt.text:
            font = n.child('font')
            size = G._num(font.attrs.get('size'), 14) if font is not None else 14
            f = QFont(self.font_family(font.text if font is not None else None))
            f.setPixelSize(max(6, int(size * 1.33)))
            p.setFont(f)
            col = G.argb((n.child('color').attrs.get('font') if n.child('color') is not None else None)) or (255, 255, 255, 255)
            p.setPen(QColor(col[0], col[1], col[2], col[3] or 255))
            flags = H_ALIGN.get(txt.attrs.get('alignx', 'left').strip(), Qt.AlignLeft) | \
                V_ALIGN.get(txt.attrs.get('aligny', 'top').strip(), Qt.AlignTop)
            p.drawText(rect, int(flags) | int(Qt.TextWordWrap), G.node_text(n) or '')
        if item.isSelected():
            p.setPen(QPen(QColor('#ffe14a'), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect)
        elif self.outline.isChecked():
            p.setPen(QPen(QColor(120, 140, 170, 90), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect)

    @staticmethod
    def _nine_slice(p: QPainter, pm, rect: QRectF, a: dict):
        sw = min(pm.width(), int(G._num(a.get('width'), pm.width())))
        sh = min(pm.height(), int(G._num(a.get('height'), pm.height())))
        l, r = G._num(a.get('left_x')), G._num(a.get('right_x'))
        t, b = G._num(a.get('top_y')), G._num(a.get('bottom_y'))
        W, H = rect.width(), rect.height()
        kx = min(1.0, W / (l + r)) if l + r > 0 else 1.0
        ky = min(1.0, H / (t + b)) if t + b > 0 else 1.0
        dl, dr, dt, db = l * kx, r * kx, t * ky, b * ky
        sx = [0, l, sw - r, sw]
        sy = [0, t, sh - b, sh]
        dx = [0, dl, W - dr, W]
        dy = [0, dt, H - db, H]
        for j in range(3):
            for i in range(3):
                src = QRectF(sx[i], sy[j], sx[i + 1] - sx[i], sy[j + 1] - sy[j])
                dst = QRectF(dx[i], dy[j], dx[i + 1] - dx[i], dy[j + 1] - dy[j])
                if src.width() > 0 and src.height() > 0 and dst.width() > 0 and dst.height() > 0:
                    p.drawPixmap(dst, pm, src)

    # ------------------------------------------------------------------ selection
    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        stack = []
        for i, b in enumerate(self.boxes):
            it = QTreeWidgetItem([G.node_label(b.node)])
            it.setData(0, Qt.UserRole, i)
            if not b.visible:
                it.setForeground(0, QColor(DARK['faint']))
            while stack and stack[-1][0] >= b.depth:
                stack.pop()
            (stack[-1][1].addChild(it) if stack else self.tree.addTopLevelItem(it))
            stack.append((b.depth, it))
        self.tree.expandToDepth(1)
        self.tree.blockSignals(False)

    def _tree_selection(self):
        if getattr(self, '_syncing', False):
            return
        idx = [t.data(0, Qt.UserRole) for t in self.tree.selectedItems()]
        if len(idx) > 1:
            self._syncing = True
            self.scene.clearSelection()
            for i in idx:
                if self.items[i] is not None:
                    self.items[i].setSelected(True)
            self._syncing = False

    def select(self, index, from_scene: bool = False):
        if index is None or index >= len(self.boxes):
            return
        self.sel = index
        self.keep_selected = self.selected_indices() if from_scene else [index]
        if not from_scene:
            self.scene.clearSelection()
            it = self.items[index]
            if it is not None:
                it.setSelected(True)
                self.view.ensureVisible(it, 40, 40)
        # tree
        found = self.tree.findItems('*', Qt.MatchWildcard | Qt.MatchRecursive)
        for t in found:
            if t.data(0, Qt.UserRole) == index:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(t)
                self.tree.blockSignals(False)
                break
        # properties
        b = self.boxes[index]
        n = b.node
        self._filling_props = True
        x, y = G.node_pos(n)
        self.px.setValue(int(x))
        self.py.setValue(int(y))
        self.sw.setValue(int(b.w))
        self.sh.setValue(int(b.h))
        self._filling_props = False
        al = n.child('align')
        self.align_lbl.setText(f"{al.attrs.get('x', 'left')} / {al.attrs.get('y', 'top')}" if al is not None
                               else 'left / top')
        tex = n.child('texture')
        self.tex_lbl.setText(tex.text if tex is not None and tex.text else '-')
        self.tex_btn.setEnabled(tex is not None and bool(tex.text))
        self._filling_props = True
        has_text = n.attrs.get('type', '').strip() in TEXT_TYPES or n.child('text') is not None
        self.txt.setEnabled(has_text)
        self.txt.setText(G.node_text(n) or '')
        f = n.child('font')
        self.fsize.setEnabled(has_text)
        self.fsize.setValue(int(G._num(f.attrs.get('size'), 14)) if f is not None else 14)
        self.color_btn.setEnabled(has_text)
        self._filling_props = False
        self.hud.setText(f'{G.node_label(n)}   ({int(b.x)}, {int(b.y)})  {int(b.w)}×{int(b.h)}'
                         + ('' if b.visible else '  · 평소엔 숨어 있음'))
        self.view.setFocus()
