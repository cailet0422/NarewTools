"""새 리소스 추가: wizards for new blocks, furniture, items and sounds."""
from __future__ import annotations

import os
import re

from PIL import Image
from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QCompleter, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHeaderView,
                               QGroupBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QScrollArea,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .. import svc_blocks as B
from ..creator import CRAFT_STATIONS, PRODUCT_MAX, Creator, Names, Recipe
from ..gl.gltfview import GltfModel
from ..svc_models import ModelService
from .common import (IMAGE_FILTER, button, checker_pixmap, confirm, error, guarded, hbox, info, label, open_file,
                     pil_to_qimage, run_task)
from .modelview import ModelView


def _scroll(w: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.NoFrame)
    s.setWidget(w)
    return s


class ImagePick(QWidget):
    """A file picker with a thumbnail."""

    def __init__(self, text='이미지 고르기…', size=64):
        super().__init__()
        self.path = None
        self.size = size
        self.thumb = QLabel()
        self.thumb.setFixedSize(size, size)
        self.thumb.setStyleSheet('border: 1px solid #313644;')
        self.name = label('없음', faint=True)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.thumb, 0, 0, 2, 1)
        lay.addWidget(button(text, self.pick), 0, 1, alignment=Qt.AlignLeft)
        lay.addWidget(self.name, 1, 1)
        lay.setColumnStretch(1, 1)

    def pick(self):
        p = open_file('이미지', IMAGE_FILTER)
        if p:
            self.set(p)

    def set(self, path):
        self.path = path
        pm = QPixmap(path)
        self.thumb.setPixmap(checker_pixmap(pm.scaled(self.size, self.size, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        self.name.setText(os.path.basename(path))


def search_combo(combo: QComboBox | None = None, chars: int = 32) -> QComboBox:
    """Editable combo with substring completion.  The width is fixed by a character count so Qt
    does not measure thousands of entries on every layout pass."""
    combo = combo or QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    c = combo.completer()
    c.setFilterMode(Qt.MatchContains)
    c.setCompletionMode(QCompleter.PopupCompletion)
    return combo


def item_completer(parent) -> QCompleter:
    """One completer shared by many ItemFields (QLineEdit re-targets it on focus)."""
    c = QCompleter(QStringListModel(parent), parent)
    c.setFilterMode(Qt.MatchContains)
    c.setCaseSensitivity(Qt.CaseInsensitive)
    c.setMaxVisibleItems(16)
    return c


def fill_completer(c: QCompleter, items):
    c.model().setStringList([f"{it['name']}  #{it['id']} · {it['type']}" for it in items])


class ItemField(QLineEdit):
    """Item picker: type part of a name (or the number) and pick from the list."""

    def __init__(self, completer: QCompleter):
        super().__init__()
        self.setPlaceholderText('아이템 이름이나 번호를 입력해 고르기')
        self.setCompleter(completer)
        self.setClearButtonEnabled(True)

    def item_id(self) -> int | None:
        """0 when empty, None when the text is not an item."""
        t = self.text().strip()
        if not t:
            return 0
        m = re.search(r'#(\d+)', t) or re.fullmatch(r'(\d+)', t)
        return int(m.group(1)) if m else None


class RecipeBox(QGroupBox):
    def __init__(self, items: QCompleter, title='제작 레시피 (선택)'):
        super().__init__(title)
        self.setCheckable(True)
        self.setChecked(False)
        g = QGridLayout(self)
        self.station = QComboBox()
        for lbl, _, _ in CRAFT_STATIONS:
            self.station.addItem(lbl)
        self.count = QSpinBox()
        self.count.setRange(1, 999)
        self.level = QSpinBox()
        self.level.setRange(1, 100)
        for spin in (self.count, self.level):
            spin.setFixedWidth(90)
        g.addWidget(label('어디서 만드나'), 0, 0)
        g.addWidget(hbox(self.station, 'stretch'), 0, 1)
        g.addWidget(label('만들어지는 개수'), 1, 0)
        g.addWidget(hbox(self.count, 'stretch'), 1, 1)
        g.addWidget(label('필요 숙련 레벨'), 2, 0)
        g.addWidget(hbox(self.level, 'stretch'), 2, 1)
        self.rows = []
        for i in range(6):
            field, n = ItemField(items), QSpinBox()
            n.setRange(0, 999)
            n.setValue(1)
            n.setFixedWidth(90)
            g.addWidget(label(f'재료 {i + 1}'), 3 + i, 0)
            g.addWidget(hbox(field, label('개수', muted=True), n), 3 + i, 1)
            self.rows.append((field, n))
        g.setColumnStretch(1, 1)

    def recipe(self) -> Recipe | None:
        if not self.isChecked():
            return None
        ings = []
        for i, (field, n) in enumerate(self.rows, start=1):
            iid = field.item_id()
            if iid is None:
                raise ValueError(f'재료 {i}: 목록에서 아이템을 고르세요 ("{field.text()}"은(는) 아이템이 아닙니다)')
            if iid and n.value() > 0:
                ings.append((iid, n.value()))
        if not ings:
            raise ValueError('레시피 재료를 하나 이상 고르세요')
        return Recipe(station=self.station.currentIndex(), ingredients=ings, count=self.count.value(),
                      level=self.level.value())


class NamesBox(QGroupBox):
    def __init__(self, what: str):
        super().__init__('이름과 설명')
        f = QFormLayout(self)
        self.kor = QLineEdit()
        self.kor.setPlaceholderText(f'예: 파스텔 {what}')
        self.eng = QLineEdit()
        self.eng.setPlaceholderText('비우면 한국어 이름을 씀')
        self.dkor = QTextEdit()
        self.dkor.setFixedHeight(54)
        self.deng = QTextEdit()
        self.deng.setFixedHeight(54)
        f.addRow('한국어 이름', self.kor)
        f.addRow('영어 이름', self.eng)
        f.addRow('설명 (한국어)', self.dkor)
        f.addRow('설명 (영어)', self.deng)

    def names(self) -> Names:
        return Names(self.kor.text(), self.eng.text(), self.dkor.toPlainText(), self.deng.toPlainText())


class CreatePage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.cr = Creator(st)
        self.tabs = QTabWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)
        self.all_items = item_completer(self)            # every item (recipe ingredients)
        self.tmpl_items = item_completer(self)           # items usable as a template for a new item
        self.built = False

    def on_show(self, **kw):
        if not self.st.game:
            return
        if not self.built:
            self.build()
        else:
            self.refresh_ids()

    def on_state(self):
        # game or project changed: templates, free ids and merged tables all differ -> rebuild lazily
        if not self.built:
            return
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        self.built = False
        if self.isVisible():
            self.on_show()

    def reload_items(self):
        items = self.cr.items()
        fill_completer(self.all_items, items)
        fill_completer(self.tmpl_items, [it for it in items if it['type'] not in ('Brick', 'Product', 'Unknown')])

    def build(self):
        self.built = True
        self.reload_items()
        self.tabs.addTab(_scroll(self._block_tab()), '🧱 새 블록')
        self.tabs.addTab(_scroll(self._furniture_tab()), '🪑 새 가구')
        self.tabs.addTab(_scroll(self._item_tab()), '🎁 새 아이템')
        self.tabs.addTab(_scroll(self._sound_tab()), '🔊 새 사운드')
        self.tabs.addTab(self._added_tab(), '📋 만든 것')
        self.refresh_ids()

    def refresh_ids(self):
        st = self.st
        if not st.game:
            return
        self.b_id.clear()
        for i in self.cr.free_brick_ids():
            self.b_id.addItem(str(i), i)
        self.f_pid.clear()
        free_p = self.cr.free_product_ids()
        for i in sorted(free_p, key=lambda x: (x < 942, x)):
            self.f_pid.addItem(str(i), i)
        nxt = self.cr.free_item_ids()
        self.f_iid.setValue(nxt[0] if nxt else 19999)
        self.i_iid.setValue(nxt[0] if nxt else 19999)
        self.fill_added()

    def _intro(self, text: str) -> QLabel:
        lb = label(text, muted=True, wrap=True)
        return lb

    # ------------------------------------------------------------------ block
    def _block_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(self._intro('새 블록을 만듭니다. 텍스처는 블록 아틀라스의 빈 칸에 들어가고, 블록 표·아이템 표·이름이 모드에 추가됩니다. '
                                '아이콘은 텍스처로 자동 생성됩니다.'))
        self.b_names = NamesBox('블록')
        v.addWidget(self.b_names)
        box = QGroupBox('블록 설정')
        f = QFormLayout(box)
        self.b_id = QComboBox()
        self.b_tmpl = search_combo()
        for b in B.bricks(self.st):
            self.b_tmpl.addItem(f"{b['label'] or b['name']}  #{b['id']} · {b['category']}", b['id'])
        i = self.b_tmpl.findData(24)
        self.b_tmpl.setCurrentIndex(max(0, i))
        f.addRow('블록 번호', hbox(self.b_id, label('빈 번호만 고를 수 있습니다 (1~254, 옛 프리팹용 171~191 제외)', faint=True)))
        f.addRow('비슷한 블록 (단단함·채집 도구·분류를 복사)', self.b_tmpl)
        v.addWidget(box)
        tex = QGroupBox('텍스처 (128×128 권장, 크기는 자동 조절)')
        g = QGridLayout(tex)
        self.b_all, self.b_top, self.b_side, self.b_bottom = ImagePick('모든 면…'), ImagePick('위…'), ImagePick('옆…'), ImagePick('아래…')
        self.b_spec = ImagePick('반사광 (선택)…')
        g.addWidget(label('모든 면 같게', muted=True), 0, 0)
        g.addWidget(self.b_all, 0, 1)
        g.addWidget(label('또는 면마다 (비운 면은 다른 면을 씀)', muted=True), 1, 0, 1, 2)
        g.addWidget(self.b_top, 2, 0)
        g.addWidget(self.b_side, 2, 1)
        g.addWidget(self.b_bottom, 2, 2)
        g.addWidget(self.b_spec, 3, 0)
        self.b_preview = QLabel()
        self.b_preview.setFixedSize(96, 96)
        g.addWidget(label('아이콘 미리보기', muted=True), 0, 3)
        g.addWidget(self.b_preview, 1, 3, 3, 1)
        g.addWidget(button('아이콘 미리보기 갱신', self.preview_block_icon), 4, 3)
        v.addWidget(tex)
        self.b_recipe = RecipeBox(self.all_items)
        v.addWidget(self.b_recipe)
        v.addWidget(button('✨ 블록 만들기', self.make_block, primary=True), alignment=Qt.AlignLeft)
        v.addStretch(1)
        return w

    def _block_textures(self) -> dict:
        t = {}
        if self.b_all.path:
            t['all'] = self.b_all.path
        for k, p in (('top', self.b_top), ('side', self.b_side), ('bottom', self.b_bottom)):
            if p.path:
                t[k] = p.path
        return t

    @guarded
    def preview_block_icon(self):
        t = self._block_textures()
        if not t:
            error('텍스처를 먼저 고르세요')
            return
        top = t.get('top') or t.get('all') or t.get('side')
        side = t.get('side') or t.get('all') or top
        ims = [Image.open(p).convert('RGBA') for p in (top, side, side)]
        icon = Creator.cube_icon(*ims).resize((96, 96), Image.NEAREST)
        self.b_preview.setPixmap(checker_pixmap(QPixmap.fromImage(pil_to_qimage(icon))))

    @guarded
    def make_block(self):
        if not self.mw.require_project():
            return
        try:
            recipe = self.b_recipe.recipe()
        except ValueError as ex:
            error(str(ex))
            return
        r = self.cr.new_block(brick_id=self.b_id.currentData(), names=self.b_names.names(),
                              template_brick=self.b_tmpl.currentData(), textures=self._block_textures(),
                              specular=self.b_spec.path, recipe=recipe)
        self._done(f"블록을 만들었습니다: 블록·아이템 번호 {r['brick']}\n아틀라스 칸: {sorted(set(r['tiles'].values()))}"
                   + (f"\n제작 레시피 #{r['craft']}" if r['craft'] else ''))

    # ------------------------------------------------------------------ furniture
    def _furniture_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(self._intro(f'새 가구(설치물)를 만듭니다. 모델은 기존 가구를 복사해 텍스처만 바꾸거나 glTF(Blender 등)에서 가져옵니다. '
                                f'가구 번호는 엔진 한도 때문에 1~{PRODUCT_MAX} 중 빈 번호만 쓸 수 있습니다.'))
        self.f_names = NamesBox('의자')
        v.addWidget(self.f_names)
        self.products = B.products(self.st)
        box = QGroupBox('가구 설정')
        f = QFormLayout(box)
        self.f_pid = QComboBox()
        self.f_iid = QSpinBox()
        self.f_iid.setRange(1, 19999)
        self.f_tmpl = search_combo()
        for p in self.products:
            self.f_tmpl.addItem(f"{p['name']}  #{p['id']} · {p['event']}", p['id'])
        self.f_model = QLineEdit('mod_my_furniture.nwmt')
        f.addRow('가구 번호', self.f_pid)
        f.addRow('아이템 번호', self.f_iid)
        f.addRow('기능 틀 (앉기·눕기·조명 등 동작을 복사)', self.f_tmpl)
        f.addRow('새 모델 파일 이름 (Object\\ 안)', self.f_model)
        v.addWidget(box)
        mbox = QGroupBox('모델')
        mg = QGridLayout(mbox)
        self.f_copy = QRadioButton('기존 가구 모델 복사 (텍스처 바꾸기)')
        self.f_copy.setChecked(True)
        self.f_gltf = QRadioButton('glTF 파일 가져오기')
        self.f_src = search_combo()
        for p in self.products:
            if p['entry']:
                self.f_src.addItem(f"{p['name']}  #{p['id']}", p['entry'])
        self.f_src.currentIndexChanged.connect(self._fill_retex)
        self.f_retex = QTableWidget(0, 3)
        self.f_retex.setHorizontalHeaderLabels(['원래 텍스처', '바꿀 이미지', ''])
        self.f_retex.verticalHeader().hide()
        hh = self.f_retex.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.f_retex.setMinimumHeight(120)
        self.retex = {}
        self.f_gltf_path = label('파일 없음', faint=True)
        self.gltf_path = None
        self.f_view = ModelView()
        self.f_view.setMinimumSize(320, 260)
        mg.addWidget(self.f_copy, 0, 0)
        mg.addWidget(self.f_src, 1, 0)
        mg.addWidget(self.f_retex, 2, 0)
        mg.addWidget(self.f_gltf, 3, 0)
        mg.addWidget(hbox(button('glTF 고르기…', self._pick_gltf), self.f_gltf_path, 'stretch'), 4, 0)
        mg.addWidget(self.f_view, 0, 1, 5, 1)
        mg.setColumnStretch(0, 1)
        v.addWidget(mbox)
        ibox = QGroupBox('아이콘 (64×64)')
        ig = QGridLayout(ibox)
        self.f_icon = ImagePick('아이콘 이미지…')
        self.f_icon_img = None
        self.f_icon_lbl = QLabel()
        self.f_icon_lbl.setFixedSize(64, 64)
        ig.addWidget(self.f_icon, 0, 0)
        ig.addWidget(button('3D 미리보기에서 찍기', self._snap_icon), 0, 1)
        ig.addWidget(self.f_icon_lbl, 0, 2)
        v.addWidget(ibox)
        self.f_recipe = RecipeBox(self.all_items)
        v.addWidget(self.f_recipe)
        v.addWidget(button('✨ 가구 만들기', self.make_furniture, primary=True), alignment=Qt.AlignLeft)
        v.addStretch(1)
        self._fill_retex()
        return w

    def _fill_retex(self):
        entry = self.f_src.currentData()
        self.retex = {}
        self.f_retex.setRowCount(0)
        if not entry:
            return
        try:
            r = B.model_textures(self.st, entry)
        except Exception:
            return
        for t in r['textures']:
            row = self.f_retex.rowCount()
            self.f_retex.insertRow(row)
            self.f_retex.setItem(row, 0, QTableWidgetItem(t))
            self.f_retex.setItem(row, 1, QTableWidgetItem('(그대로)'))
            self.f_retex.setCellWidget(row, 2, button('이미지…', lambda _=False, t=t, row=row: self._pick_retex(t, row)))
        self._preview_model([entry])

    def _pick_retex(self, tex, row):
        p = open_file('바꿀 이미지', IMAGE_FILTER)
        if p:
            self.retex[tex] = p
            self.f_retex.setItem(row, 1, QTableWidgetItem(os.path.basename(p)))

    def _pick_gltf(self):
        p = open_file('가구 모델 glTF', 'glTF (*.glb *.gltf)')
        if p:
            self.gltf_path = p
            self.f_gltf_path.setText(os.path.basename(p))
            self.f_gltf.setChecked(True)
            run_task(lambda: GltfModel(p), done=lambda m: self.f_view.set_model(m), busy='glTF 미리보기')

    def _preview_model(self, names):
        ms = ModelService(self.st)
        run_task(lambda: GltfModel(ms.glb(names, [], None)), done=lambda m: self.f_view.set_model(m), busy='3D 미리보기')

    def _snap_icon(self):
        img = self.f_view.snapshot(256)
        tmp = os.path.join(self.st.cache_dir, 'icon_snapshot.png')
        img.save(tmp)
        im = Image.open(tmp).convert('RGBA')
        # treat the background colour as transparent
        bg = im.getpixel((2, 2))
        im.putdata([(r, g, b, 0) if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) < 12 else (r, g, b, a)
                    for r, g, b, a in im.getdata()])
        im = im.resize((64, 64), Image.LANCZOS)
        self.f_icon_img = im
        self.f_icon_lbl.setPixmap(checker_pixmap(QPixmap.fromImage(pil_to_qimage(im))))

    @guarded
    def make_furniture(self):
        if not self.mw.require_project():
            return
        try:
            recipe = self.f_recipe.recipe()
        except ValueError as ex:
            error(str(ex))
            return
        icon = self.f_icon.path or self.f_icon_img
        if icon is None:
            if not confirm('아이콘 없음', '아이콘이 없습니다. 3D 미리보기에서 자동으로 찍을까요?', '찍기'):
                return
            self._snap_icon()
            icon = self.f_icon_img
        kw = dict(product_id=self.f_pid.currentData(), item_id=self.f_iid.value(), names=self.f_names.names(),
                  template_product=self.f_tmpl.currentData(), model_name=self.f_model.text(), icon=icon, recipe=recipe)
        if self.f_gltf.isChecked():
            if not self.gltf_path:
                error('glTF 파일을 고르세요')
                return
            kw['gltf'] = self.gltf_path
        else:
            kw['copy_model'] = self.f_src.currentData()
            kw['retexture'] = dict(self.retex)
        r = self.cr.new_furniture(**kw)
        self._done(f"가구를 만들었습니다: 가구 #{r['product']}, 아이템 #{r['item']}\n모델: {r['model']}"
                   + (f"\n제작 레시피 #{r['craft']}" if r['craft'] else ''))

    # ------------------------------------------------------------------ item
    def _item_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(self._intro('기존 아이템(음식·물약·재료·도구 등)을 틀로 새 아이템을 만듭니다. 효과·수치는 틀 아이템을 따르며, '
                                '리소스 편집의 XML\\item_table.xml(합쳐진 결과)에서 더 고칠 수 있습니다.'))
        self.i_names = NamesBox('물약')
        v.addWidget(self.i_names)
        box = QGroupBox('아이템 설정')
        f = QFormLayout(box)
        self.i_iid = QSpinBox()
        self.i_iid.setRange(1, 19999)
        self.i_tmpl = ItemField(self.tmpl_items)
        self.i_icon = ImagePick('아이콘 이미지…')
        f.addRow('아이템 번호', self.i_iid)
        f.addRow('틀 아이템', self.i_tmpl)
        f.addRow('아이콘', self.i_icon)
        v.addWidget(box)
        self.i_recipe = RecipeBox(self.all_items)
        v.addWidget(self.i_recipe)
        v.addWidget(button('✨ 아이템 만들기', self.make_item, primary=True), alignment=Qt.AlignLeft)
        v.addStretch(1)
        return w

    @guarded
    def make_item(self):
        if not self.mw.require_project():
            return
        if not self.i_tmpl.item_id():
            error('틀 아이템을 고르세요')
            return
        if not self.i_icon.path:
            error('아이콘 이미지를 고르세요')
            return
        try:
            recipe = self.i_recipe.recipe()
        except ValueError as ex:
            error(str(ex))
            return
        r = self.cr.new_item(item_id=self.i_iid.value(), names=self.i_names.names(), template_item=self.i_tmpl.item_id(),
                             icon=self.i_icon.path, recipe=recipe)
        self._done(f"아이템을 만들었습니다: #{r['item']} ({r['type']})" + (f"\n제작 레시피 #{r['craft']}" if r['craft'] else ''))

    # ------------------------------------------------------------------ sound
    def _sound_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(self._intro('새 사운드 파일을 추가하고, 날씨·시간별 배경음이나 UI 효과음으로 연결합니다. '
                                '기존 사운드를 바꾸려면 리소스 편집에서 해당 파일을 교체하세요.'))
        box = QGroupBox('사운드 파일')
        f = QFormLayout(box)
        self.s_path = None
        self.s_file = label('파일 없음', faint=True)
        self.s_name = QLineEdit('Sound\\mod\\my_bgm.ogg')
        self.s_vol = QDoubleSpinBox()
        self.s_vol.setRange(0.0, 1.0)
        self.s_vol.setSingleStep(0.05)
        self.s_vol.setValue(0.1)
        f.addRow('파일 (.wav/.ogg/.mp3)', hbox(button('고르기…', self._pick_sound), self.s_file, 'stretch'))
        f.addRow('게임 안 이름', self.s_name)
        f.addRow('배경음 볼륨', self.s_vol)
        v.addWidget(box)
        sky = QGroupBox('배경음으로 쓸 날씨·시간')
        sl = QVBoxLayout(sky)
        self.s_sky = QListWidget()
        self.s_sky.setMinimumHeight(180)
        for s in self.cr.sky_slots():
            it = QListWidgetItem(f"{s['name']}   (지금: {s['bgm']})")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, (s['time'], s['weather']))
            self.s_sky.addItem(it)
        sl.addWidget(self.s_sky)
        v.addWidget(sky)
        ui = QGroupBox('UI 효과음으로 쓸 곳')
        ul = QVBoxLayout(ui)
        self.s_ui = QListWidget()
        self.s_ui.setMinimumHeight(140)
        for s in self.cr.ui_sound_slots():
            it = QListWidgetItem(f"{s['type']}   (지금: {s['file']})")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, s['type'])
            self.s_ui.addItem(it)
        ul.addWidget(self.s_ui)
        v.addWidget(ui)
        v.addWidget(button('✨ 사운드 추가', self.make_sound, primary=True), alignment=Qt.AlignLeft)
        v.addStretch(1)
        return w

    def _pick_sound(self):
        p = open_file('사운드 파일', '사운드 (*.wav *.ogg *.mp3)')
        if p:
            self.s_path = p
            self.s_file.setText(os.path.basename(p))
            ext = os.path.splitext(p)[1].lower()
            base = os.path.splitext(os.path.basename(p))[0]
            safe = ''.join(c if c.isascii() and (c.isalnum() or c in '_-') else '_' for c in base) or 'sound'
            self.s_name.setText(f'Sound\\mod\\{safe}{ext}')

    @guarded
    def make_sound(self):
        if not self.mw.require_project():
            return
        if not self.s_path:
            error('사운드 파일을 고르세요')
            return
        sky = [self.s_sky.item(i).data(Qt.UserRole) for i in range(self.s_sky.count())
               if self.s_sky.item(i).checkState() == Qt.Checked]
        ui = [self.s_ui.item(i).data(Qt.UserRole) for i in range(self.s_ui.count())
              if self.s_ui.item(i).checkState() == Qt.Checked]
        r = self.cr.new_sound(src=self.s_path, entry=self.s_name.text().strip(), sky=sky, ui=ui,
                              volume=self.s_vol.value() if sky else None)
        self._done(f"사운드를 추가했습니다: {r['entry']}" + ('\n연결: ' + ', '.join(r['used']) if r['used'] else ''))

    # ------------------------------------------------------------------ added list
    def _added_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(self._intro('이 모드가 게임 표에 추가하거나 바꾼 항목입니다. 지우면 그 항목만 모드에서 빠집니다 '
                                '(텍스처·모델 파일은 "내 모드"에서 따로 뺄 수 있습니다).'))
        self.added = QTableWidget(0, 4)
        self.added.setHorizontalHeaderLabels(['표', '항목', '이름', ''])
        self.added.horizontalHeader().setStretchLastSection(True)
        self.added.setColumnWidth(0, 220)
        self.added.setColumnWidth(1, 220)
        self.added.setColumnWidth(2, 260)
        v.addWidget(self.added, 1)
        return w

    def fill_added(self):
        if not self.built:
            return
        rows = self.cr.added() if self.st.project else []
        self.added.setRowCount(len(rows))
        for r, a in enumerate(rows):
            self.added.setItem(r, 0, QTableWidgetItem(a['table']))
            self.added.setItem(r, 1, QTableWidgetItem(f"{a['tag']} {' / '.join(a['key'])}"))
            self.added.setItem(r, 2, QTableWidgetItem(a['label']))
            self.added.setCellWidget(r, 3, button('지우기', lambda _=False, a=a: self._remove(a), danger=True))

    def _remove(self, a):
        if confirm('항목 지우기', f"{a['table']}의 {a['tag']} {'/'.join(a['key'])} 항목을 모드에서 뺄까요?", '빼기', danger=True):
            self.st.remove_record(a['table'], a['tag'], a['key'])
            self.mw.refresh_header()
            self.fill_added()

    def _done(self, msg: str):
        self.reload_items()
        self.mw.refresh_header()
        self.refresh_ids()
        info(msg + '\n\n게임에 넣으려면 "내 모드 → 모드 매니저에 설치" 후 NarewModManager에서 적용하세요.')
