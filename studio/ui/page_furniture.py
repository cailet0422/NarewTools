"""가구 텍스처: products from XML\\product.xml -> their model -> material textures (+ 3D preview)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QVBoxLayout,
                               QWidget)

from .. import svc_blocks as B, svc_resources as R
from ..gl.gltfview import GltfModel
from ..svc_models import ModelService
from .common import IMAGE_FILTER, badge, button, debounce, hbox, label, open_file, run_task, toast
from .editors import ResourceEditor
from .modelview import ModelView

TEX_KO = {'diffuse_map': '색상', 'normal_map': '노멀', 'specular_map': '반사광', 'mask_map': '마스크'}


class FurniturePage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.products = []
        self.current = None
        self.models = ModelService(st)
        self.search = QLineEdit()
        self.search.setPlaceholderText('가구 이름 검색 (예: 의자, 침대)')
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda it, _: it and self.select(it.data(Qt.UserRole)))
        self._t = debounce(150, self.fill)
        self.search.textChanged.connect(lambda: self._t.start())
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(self.search)
        ll.addWidget(self.list, 1)
        self.title = label('가구를 고르세요', title=True)
        self.mats = QWidget()
        self.mlay = QVBoxLayout(self.mats)
        self.mlay.setContentsMargins(0, 0, 0, 0)
        self.view = ModelView()
        self.view.setMinimumHeight(260)
        top = QWidget()
        tl = QGridLayout(top)
        tl.addWidget(self.title, 0, 0, 1, 2)
        tl.addWidget(self.mats, 1, 0)
        tl.addWidget(self.view, 1, 1)
        tl.setColumnStretch(0, 3)
        tl.setColumnStretch(1, 2)
        self.editor = ResourceEditor(st)
        self.editor.changed.connect(lambda _: self.select(self.current, keep_editor=True))
        right = QSplitter(Qt.Vertical)
        right.addWidget(top)
        right.addWidget(self.editor)
        right.setSizes([360, 560])
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([300, 1100])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    def on_show(self, **kw):
        if not self.st.game:
            return
        if not self.products:
            self.reload()

    def on_state(self):
        self.products = []
        if self.isVisible():
            self.reload()

    def reload(self):
        def done(ps):
            self.products = ps
            self.fill()
        run_task(B.products, self.st, done=done, busy='가구 목록')

    def fill(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for p in self.products:
            text = f"#{p['id']}  {p['name'] or '(이름 없음)'}"
            if q and q not in (text + ' ' + p['model']).lower():
                continue
            it = QListWidgetItem(text + ('' if p['entry'] else '   (모델 없음)'))
            it.setData(Qt.UserRole, p)
            self.list.addItem(it)

    def _clear(self):
        while self.mlay.count():
            it = self.mlay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def select(self, p, keep_editor=False):
        if not p:
            return
        self.current = p
        self._clear()
        self.title.setText(f"{p['name']} (#{p['id']})")
        if not p['entry']:
            self.mlay.addWidget(label(f"모델 파일(Object\\{p['model']})을 찾지 못했습니다.", muted=True))
            return
        r = B.model_textures(self.st, p['entry'])
        self.mlay.addWidget(hbox(label('모델', faint=True), label(r['model'], selectable=True),
                                 button('모델 뷰어', lambda: self.mw.open_page('models', name=r['model'])),
                                 button('XML 편집', lambda: self.mw.open_page('resources', name=r['model'])), 'stretch'))
        for m in r['materials']:
            card = QFrame()
            card.setObjectName('card')
            g = QGridLayout(card)
            g.addWidget(label(f"재질 {m['id']}: {m['name']}", muted=True), 0, 0, 1, 4)
            for row, (tag, t) in enumerate(m['textures'].items(), start=1):
                g.addWidget(label(TEX_KO.get(tag, tag), faint=True), row, 0)
                g.addWidget(label(t.get('entry') or t.get('expected') or t['ref'], selectable=True), row, 1)
                if t['status'] == 'unused':
                    g.addWidget(label('엔진 미사용', faint=True), row, 2)
                elif t['status'] == 'missing':
                    g.addWidget(label('파일 없음', faint=True), row, 2)
                    g.addWidget(button('이미지 넣기…', lambda _=False, e=t['expected']: self.add_missing(e)), row, 3)
                else:
                    g.addWidget(badge(t['status']), row, 2)
                    g.addWidget(button('편집', lambda _=False, e=t['entry']: self.editor.open(e)), row, 3)
            g.setColumnStretch(1, 1)
            self.mlay.addWidget(card)
        self.mlay.addStretch(1)
        if not keep_editor and r['textures']:
            self.editor.open(r['textures'][0])
        names = [p['entry']]

        def build():
            info = self.models.info(names)
            return GltfModel(self.models.glb(names, [], info['skeleton']))
        run_task(build, done=lambda m: self.view.set_model(m, keep_camera=keep_editor), busy='3D 미리보기')

    def add_missing(self, name):
        if not self.mw.require_project():
            return
        path = open_file('텍스처 이미지', IMAGE_FILTER)
        if path:
            R.replace_from_path(self.st, name, path)
            toast('텍스처를 넣었습니다: ' + name)
            self.mw.refresh_header()
            self.select(self.current)
            self.editor.open(name)

    def can_leave(self):
        return self.editor.can_leave()

    def shutdown(self):
        self.editor.stop_audio()
