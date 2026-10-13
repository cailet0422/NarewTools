"""모델 뷰어: compose model parts, play animations, Blender round trip, animation speed."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLineEdit, QListWidget, QListWidgetItem,
                               QSlider, QSplitter, QVBoxLayout, QWidget)

from ..gl.gltfview import GltfModel
from ..svc_models import ModelService
from .common import (button, confirm, debounce, hbox, info, label, open_file, run_task, save_file, toast)
from .modelview import ModelView


class ModelsPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.svc = ModelService(st)
        self.all = []
        self.parts: list[str] = []
        self.info = None
        self.anim = None
        # left: model list
        self.cat = QComboBox()
        self.cat.addItem('모든 종류', '')
        self.search = QLineEdit()
        self.search.setPlaceholderText('모델 이름 검색')
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda it: self.add_part(it.data(Qt.UserRole)))
        self.list.itemClicked.connect(lambda it: self.set_parts([it.data(Qt.UserRole)]))
        self._t = debounce(150, self.fill)
        self.search.textChanged.connect(lambda: self._t.start())
        self.cat.currentIndexChanged.connect(self.fill)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(self.cat)
        ll.addWidget(self.search)
        ll.addWidget(self.list, 1)
        ll.addWidget(label('클릭: 보기 · 더블클릭: 장면에 추가 (옷·머리·신발 조합)', faint=True, wrap=True))
        # center: view + controls
        self.parts_bar = QWidget()
        self.parts_lay = None
        self.view = ModelView()
        self.view.frame_changed.connect(self._frame)
        self.overlay = label('왼쪽에서 모델을 고르세요. 왼쪽 드래그=회전, 오른쪽 드래그=이동, 휠=확대', muted=True)
        self.timeline = QSlider(Qt.Horizontal)
        self.timeline.setRange(0, 1000)
        self.timeline.sliderMoved.connect(self._seek)
        self.time_lbl = label('', muted=True)
        self.play_btn = button('⏸', self.toggle_play)
        self.speed = QComboBox()
        for s in (0.25, 0.5, 1.0, 1.5, 2.0):
            self.speed.addItem(f'{s}배', s)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda: setattr(self.view, 'speed', self.speed.currentData()))
        self.skel = QCheckBox('뼈대')
        self.skel.toggled.connect(lambda v: self._ren_flag('show_skeleton', v))
        self.wire = QCheckBox('와이어프레임')
        self.wire.toggled.connect(lambda v: self._ren_flag('wire', v))
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.head = QFrame()
        self.head.setObjectName('head')
        self.head_lay = QVBoxLayout(self.head)
        self.head_lay.setContentsMargins(10, 6, 10, 6)
        self.head_lay.addWidget(self.overlay)
        cl.addWidget(self.head)
        cl.addWidget(self.view, 1)
        cl.addWidget(hbox(self.play_btn, self.timeline, self.time_lbl, label('미리보기 속도', muted=True), self.speed,
                          self.skel, self.wire, button('화면 맞춤', self.view.reframe), margins=(8, 6, 8, 6)))
        # right: animations + edit
        self.anim_search = QLineEdit()
        self.anim_search.setPlaceholderText('애니메이션 검색')
        self.anim_list = QListWidget()
        self.anim_list.itemClicked.connect(lambda it: self.set_anim(it.data(Qt.UserRole)))
        self.anim_search.textChanged.connect(self.fill_anims)
        self.edit = QWidget()
        self.elay = QVBoxLayout(self.edit)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.addWidget(label('애니메이션', title=True))
        rl.addWidget(self.anim_search)
        rl.addWidget(self.anim_list, 1)
        rl.addWidget(self.edit)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setSizes([300, 900, 320])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    # ------------------------------------------------------------------ list
    def on_show(self, name: str | None = None):
        if not self.st.game:
            return
        if not self.all:
            self.all = self.svc.list_models()
            cats = sorted({m['category'] for m in self.all})
            self.cat.blockSignals(True)
            for c in cats:
                self.cat.addItem(c, c)
            self.cat.blockSignals(False)
            self.fill()
        if name and name.lower().endswith('.nwmt'):
            self.set_parts([name])

    def on_state(self):
        self.all = []
        if self.isVisible():
            self.on_show()

    def fill(self):
        q, c = self.search.text().strip().lower(), self.cat.currentData()
        self.list.clear()
        n = 0
        for m in self.all:
            if (c and m['category'] != c) or (q and q not in m['name'].lower()):
                continue
            it = QListWidgetItem(m['name'].split('\\')[-1] + ('  ●' if m['modified'] else ''))
            it.setToolTip(m['name'] + '\n' + m['category'])
            it.setData(Qt.UserRole, m['name'])
            self.list.addItem(it)
            n += 1
            if n >= 3000:
                break

    # ------------------------------------------------------------------ scene
    def _render_parts(self):
        while self.head_lay.count() > 1:
            it = self.head_lay.takeAt(1)
            if it.widget():
                it.widget().deleteLater()
        if self.parts:
            chips = [button(p.split('\\')[-1] + '  ×', lambda _=False, p=p: self.set_parts([x for x in self.parts if x != p]),
                            tip='장면에서 빼기') for p in self.parts]
            self.head_lay.addWidget(hbox(label('장면:', muted=True), *chips, 'stretch'))

    def add_part(self, name):
        if name.lower() not in (p.lower() for p in self.parts):
            self.set_parts(self.parts + [name])

    def set_parts(self, parts, keep_anim: bool = False):
        self.parts = parts
        if not keep_anim:
            self.anim = None
        self._render_parts()
        if not parts:
            self.view.set_model(None)
            self.info = None
            self.fill_anims()
            self.fill_edit()
            return

        def job():
            return self.svc.info(parts)

        def done(info):
            self.info = info
            self.fill_anims()
            self.fill_edit()
            self.load_scene(False)
        run_task(job, done=done, busy='모델 정보')

    def load_scene(self, keep_camera: bool):
        parts, anim = list(self.parts), self.anim
        skel = self.info['skeleton'] if self.info else None

        def job():
            return GltfModel(self.svc.glb(parts, [anim] if anim else [], skel))

        def done(model):
            self.view.set_model(model, keep_camera)
            self.view.playing = True
            self.play_btn.setText('⏸')
            s = self.view.ren.stats() if self.view.ren else {'meshes': len(model.prims), 'tris': 0, 'bones': 0}
            self.overlay.setText(f"메시 {s['meshes']} · 삼각형 {s['tris']:,} · 뼈 {s['bones']}"
                                 + (f" · 뼈대 {skel.split(chr(92))[-1]}" if skel else '')
                                 + (f" · 애니메이션 {model.animations[0].duration:.2f}초" if model.animations else ''))
        run_task(job, done=done, busy='모델 변환')

    def _ren_flag(self, name, value):
        if self.view.ren:
            setattr(self.view.ren, name, value)
            self.view.update()

    def toggle_play(self):
        self.view.playing = not self.view.playing
        self.play_btn.setText('⏸' if self.view.playing else '▶')

    def _frame(self, t, d):
        self.timeline.blockSignals(True)
        self.timeline.setValue(int(t / d * 1000) if d else 0)
        self.timeline.blockSignals(False)
        self.time_lbl.setText(f'{t:.2f} / {d:.2f}초')

    def _seek(self, v):
        if self.view.anim:
            self.view.playing = False
            self.play_btn.setText('▶')
            self.view.seek(self.view.anim.duration * v / 1000)

    # ------------------------------------------------------------------ animations
    def fill_anims(self):
        self.anim_list.clear()
        if not self.info:
            return
        q = self.anim_search.text().strip().lower()
        none = QListWidgetItem('(애니메이션 없음)')
        none.setData(Qt.UserRole, None)
        self.anim_list.addItem(none)
        for a in self.info['anims']:
            if q and q not in a['name'].lower():
                continue
            it = QListWidgetItem(a['name'].split('\\')[-1] + ('  ●' if a['status'] != 'original' else ''))
            it.setToolTip(a['name'])
            it.setData(Qt.UserRole, a['name'])
            self.anim_list.addItem(it)
            if a['name'] == self.anim:
                it.setSelected(True)

    def set_anim(self, name):
        self.anim = name
        self.fill_edit()
        self.load_scene(True)

    # ------------------------------------------------------------------ edit panel
    def fill_edit(self):
        while self.elay.count():
            it = self.elay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not self.info:
            return
        self.elay.addWidget(label('수정하기', title=True))
        self.elay.addWidget(button('glTF로 내보내기 (Blender용)…', self.export_gltf))
        self.elay.addWidget(button('glTF에서 모델 가져오기…', self.import_model))
        if self.anim:
            self.elay.addWidget(button('glTF에서 이 애니메이션 가져오기…', self.import_anim))
        if self.info.get('skeleton'):
            self.elay.addWidget(button('glTF에서 새 애니메이션 추가…', self.import_new_anim))
        self.elay.addWidget(label('Blender: 가져오기 전에 장면을 30fps로, 내보낼 때 Include → Custom Properties를 켜세요.',
                                  faint=True, wrap=True))
        if self.anim:
            sp = QDoubleSpinBox()
            sp.setRange(0.25, 4.0)
            sp.setSingleStep(0.25)
            sp.setValue(1.0)
            sp.setSuffix(' 배')

            def apply():
                if not self.mw.require_project():
                    return
                old, new = self.svc.set_anim_speed(self.anim, sp.value())
                toast(f'게임 속 재생 속도를 바꿨습니다 (키당 {old:g}ms → {new}ms)')
                self.mw.refresh_header()
                self.set_parts(self.parts, keep_anim=True)
            self.elay.addWidget(label('게임 속 재생 속도 (원본 기준)', muted=True))
            self.elay.addWidget(hbox(sp, button('속도 적용', apply, primary=True)))
            self.elay.addWidget(button('이 애니메이션 XML 편집', lambda: self.mw.open_page('resources', name=self.anim)))
        for m in self.info['models']:
            self.elay.addWidget(button(f"{m['name'].split(chr(92))[-1]} XML 편집",
                                       lambda _=False, n=m['name']: self.mw.open_page('resources', name=n)))

    def export_gltf(self):
        base = self.parts[0].split('\\')[-1].rsplit('.', 1)[0] + ('_' + self.anim.split('\\')[-1].rsplit('.', 1)[0] if self.anim else '')
        p = save_file('glTF 내보내기', base + '.glb', 'glTF 바이너리 (*.glb);;glTF (*.gltf)')
        if not p:
            return
        parts, anim, skel = list(self.parts), self.anim, self.info['skeleton']
        run_task(lambda: self.svc.export(parts, [anim] if anim else [], skel, p),
                 done=lambda path: toast('저장했습니다: ' + path), busy='glTF 내보내기')

    def import_model(self):
        if not self.mw.require_project():
            return
        src = open_file('가져올 glTF', 'glTF (*.glb *.gltf)')
        if not src:
            return
        saved, skipped = self.svc.import_models(self.parts, src)
        info(f'가져왔습니다: {len(saved)}개 파일' + (f'\n건너뜀: {", ".join(skipped)}' if skipped else ''))
        self.mw.refresh_header()
        self.set_parts(self.parts)

    def import_anim(self):
        if not self.mw.require_project():
            return
        src = open_file('가져올 glTF', 'glTF (*.glb *.gltf)')
        if src:
            self.svc.import_anim(self.anim, src, self.info['skeleton'])
            toast('애니메이션을 바꿨습니다')
            self.mw.refresh_header()
            self.load_scene(True)

    def import_new_anim(self):
        from .common import ask_text
        if not self.mw.require_project():
            return
        src = open_file('가져올 glTF', 'glTF (*.glb *.gltf)')
        if not src:
            return
        name = ask_text('새 애니메이션', '파일 이름 (.nwat)', 'my_anim.nwat')
        if not name:
            return
        d = self.info['skeleton'].rsplit('\\', 1)[0]
        target = d + '\\' + (name if name.lower().endswith('.nwat') else name + '.nwat')
        if any(a['name'].lower() == target.lower() for a in self.info['anims']) and \
                not confirm('덮어쓰기', f'{target}이(가) 이미 있습니다. 바꿀까요?', '바꾸기', danger=True):
            return
        self.svc.import_anim(target, src, self.info['skeleton'])
        info(f'새 애니메이션을 추가했습니다: {target}\n게임에서 쓰려면 이 이름을 참조하는 XML도 수정해야 합니다.')
        self.mw.refresh_header()
        self.anim = target
        self.set_parts(self.parts, keep_anim=True)
