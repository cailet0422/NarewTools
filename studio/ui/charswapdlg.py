"""캐릭터 교체: pick a 3D model file and put it in place of the Islet player character (both genders)."""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QLineEdit, QSplitter,
                               QVBoxLayout, QWidget)

from narew import convert, scene3d

from ..core import StudioError
from ..gl.gltfview import GltfModel
from ..svc_charswap import GENDERS, CharSwapService, formats_text, preview_dir
from .common import button, confirm, error, hbox, info, label, open_file, run_task
from .modelview import ModelView

PREVIEW_ANIMS = (('대기', 'noncombat_idle'), ('걷기', 'noncombat_walk'), ('달리기', 'common_run'),
                 ('점프', 'common_e_jump'), ('춤', 'common_e_dance_01'), ('나무 베기', 'noncombat_cutting_wood'),
                 ('공격', 'combat_attack_onewp01'), ('의자에 앉기', 'noncombat_idle_chair01'),
                 ('수영', 'common_swim'), ('말 타기', 'noncombat_riding'))


def file_filter() -> str:
    exts = ' '.join('*' + e for e in scene3d.supported_extensions())
    return f'3D 모델 ({exts});;모든 파일 (*.*)'


class CharSwapDialog(QDialog):
    def __init__(self, parent, st, mw):
        super().__init__(parent)
        self.st, self.mw = st, mw
        self.svc = CharSwapService(st)
        self.setWindowTitle('캐릭터 교체')
        self.resize(1180, 760)
        self.cfg = dict(st.settings.get('charswap', {}))
        self.src = self.cfg.get('src') if self.cfg.get('src') and os.path.isfile(self.cfg.get('src')) else None
        self.tmp = None                 # dry-run folder of the current preview
        self.applied = False

        self.file_lbl = label('', wrap=True)
        self.info_lbl = label('', muted=True, wrap=True, selectable=True)
        self.f1 = QCheckBox('여자 캐릭터 (F1)')
        self.m1 = QCheckBox('남자 캐릭터 (M1)')
        self.npcs = QCheckBox('사람 NPC도 같이 바꾸기 (같은 옷·머리를 입기 때문)')
        self.hide = QCheckBox('원래 얼굴·머리·옷·신발·모자·장신구 숨기기')
        for w, k in ((self.f1, 'f1'), (self.m1, 'm1'), (self.npcs, 'npcs'), (self.hide, 'hide')):
            w.setChecked(self.cfg.get(k, True))
        self.height = QDoubleSpinBox()
        self.height.setRange(0.0, 6.0)
        self.height.setSingleStep(0.05)
        self.height.setDecimals(2)
        self.height.setSpecialValueText('원래 캐릭터 키')
        self.height.setValue(self.cfg.get('height', 0.0))
        self.arm = QDoubleSpinBox()
        self.arm.setRange(0.0, 40.0)
        self.arm.setSuffix(' °')
        self.arm.setValue(self.cfg.get('arm_angle', 12.0))
        self.leg = QDoubleSpinBox()
        self.leg.setRange(-1.0, 20.0)
        self.leg.setSuffix(' °')
        self.leg.setSpecialValueText('자동')
        self.leg.setValue(self.cfg.get('leg_angle', -1.0))
        self.turn = QComboBox()
        for t in (0, 90, 180, 270):
            self.turn.addItem(f'{t}°' if t else '그대로', t)
        self.turn.setCurrentIndex(max(0, self.turn.findData(self.cfg.get('turn', 0))))
        self.blender = QLineEdit(self.cfg.get('blender', '') or '')
        self.blender.setPlaceholderText(scene3d.find_blender() or 'DAE·3DS·BLEND·USD 등에만 필요 (blender.exe)')
        self.anim = QComboBox()
        for lbl, a in PREVIEW_ANIMS:
            self.anim.addItem(lbl, a)
        self.anim.setCurrentIndex(2)
        self.pv_gender = QComboBox()
        for g, c in GENDERS.items():
            self.pv_gender.addItem(c['label'], g)
        self.anim.currentIndexChanged.connect(self.show_preview)
        self.pv_gender.currentIndexChanged.connect(self.show_preview)

        form = QFormLayout()
        form.addRow('키', self.height)
        form.addRow('팔을 몸쪽으로', self.arm)
        form.addRow('다리 벌리기', self.leg)
        form.addRow('방향 돌리기', self.turn)
        form.addRow('Blender', hbox(self.blender, button('찾기…', self.pick_blender)))
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(label('모델 파일', title=True))
        ll.addWidget(hbox(button('파일 고르기…', self.pick_file, primary=True), 'stretch'))
        ll.addWidget(self.file_lbl)
        ll.addWidget(label(formats_text(), faint=True, wrap=True))
        ll.addWidget(self.info_lbl, 1)
        ll.addWidget(label('바꿀 캐릭터', title=True))
        for w in (self.f1, self.m1, self.npcs, self.hide):
            ll.addWidget(w)
        ll.addLayout(form)
        ll.addWidget(label('아일렛 동작을 그대로 씁니다: 모델의 팔·다리를 아일렛 기본 자세 각도로 맞추고, 뼈 길이와 '
                           '몸 높이에 맞게 모든 동작을 고칩니다. 원본 게임 파일은 바뀌지 않고 모드 프로젝트에만 저장됩니다.',
                           faint=True, wrap=True))
        self.view = ModelView()
        self.pv_lbl = label('파일을 고르고 "미리보기"를 누르세요', muted=True)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(hbox(self.pv_lbl, 'stretch', label('동작', muted=True), self.anim, self.pv_gender,
                          button('미리보기', self.preview), margins=(8, 6, 8, 6)))
        rl.addWidget(self.view, 1)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([420, 760])
        self.apply_btn = button('모드에 적용', self.apply, primary=True)
        self.remove_btn = button('교체 되돌리기', self.remove, danger=True)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        self.status_lbl = label('', muted=True, wrap=True)
        lay.addWidget(hbox(self.status_lbl, 'stretch', self.remove_btn, self.apply_btn, button('닫기', self.close)))
        self.refresh_status()
        if self.src:
            self.set_file(self.src)

    # ------------------------------------------------------------------ state
    def options(self) -> dict:
        genders = [g for g, w in (('f1', self.f1), ('m1', self.m1)) if w.isChecked()]
        return dict(genders=genders, height=self.height.value() or None, arm_angle=self.arm.value(),
                    leg_angle=None if self.leg.value() < 0 else self.leg.value(), npcs=self.npcs.isChecked(),
                    hide=self.hide.isChecked(), turn=float(self.turn.currentData() or 0),
                    blender=self.blender.text().strip() or None)

    def save_cfg(self):
        o = self.options()
        self.st.settings['charswap'] = {'src': self.src, 'f1': 'f1' in o['genders'], 'm1': 'm1' in o['genders'],
                                        'npcs': o['npcs'], 'hide': o['hide'], 'height': self.height.value(),
                                        'arm_angle': o['arm_angle'], 'leg_angle': self.leg.value(),
                                        'turn': o['turn'], 'blender': o['blender'] or ''}
        self.st.save_settings()

    def refresh_status(self):
        s = self.svc.status() if self.st.project else None
        self.remove_btn.setEnabled(bool(s))
        if s:
            self.status_lbl.setText(f"이 모드: {os.path.basename(s['source'])}(으)로 교체됨 · {s['time']} · "
                                    f"파일 {len(s.get('written', []))}개")
        elif self.st.project:
            self.status_lbl.setText('이 모드에는 아직 캐릭터 교체가 없습니다')
        else:
            self.status_lbl.setText('모드 프로젝트가 없습니다 (적용할 때 만들 수 있어요)')

    # ------------------------------------------------------------------ file
    def pick_blender(self):
        p = open_file('blender.exe', 'Blender (blender.exe);;모든 파일 (*.*)')
        if p:
            self.blender.setText(p)

    def pick_file(self):
        p = open_file('교체할 3D 모델', file_filter(), os.path.dirname(self.src) if self.src else '')
        if p:
            self.set_file(p)

    def set_file(self, path):
        self.src = path
        self.file_lbl.setText(path)
        self.info_lbl.setText('읽는 중…')
        o = self.options()

        def done(r):
            lines = [f"{r['format']} · 메시 {r['meshes']} · 정점 {r['vertices']:,} · 삼각형 {r['triangles']:,} · "
                     f"뼈 {r['bones']} · 재질 {len(r['materials'])}"]
            if r['humanoid']:
                m = r.get('mapping', {})
                lines.append('✔ 사람 뼈대 인식: ' + ', '.join(f'{k}={v}' for k, v in list(m.items())[:8])
                             + (' …' if len(m) > 8 else ''))
            else:
                lines.append('✖ 사람 뼈대를 찾지 못함')
            miss = [x['name'] for x in r['materials'] if not x['has_image']]
            if miss:
                lines.append('텍스처 없음: ' + ', '.join(miss))
            lines += r.get('notes', [])
            self.info_lbl.setText('\n'.join(lines))
            self.preview()

        def failed(msg):
            self.info_lbl.setText('읽지 못했습니다: ' + msg)
        run_task(self.svc.inspect, path, o['blender'], o['turn'], done=done, failed=failed, busy='모델 읽기')

    # ------------------------------------------------------------------ preview
    def preview(self):
        if not self.src:
            return error('먼저 모델 파일을 고르세요')
        o = self.options()
        if not o['genders']:
            return error('여자/남자 중 하나 이상 고르세요')
        old = self.tmp
        d = preview_dir()

        def job():
            self.svc.build(self.src, o['genders'], o['height'], o['arm_angle'], o['leg_angle'], False, False,
                           o['turn'], o['blender'], out_dir=d)
            return d

        def done(path):
            self.tmp = path
            if old:
                shutil.rmtree(old, ignore_errors=True)
            self.show_preview()

        def failed(msg):
            shutil.rmtree(d, ignore_errors=True)
            error(msg)
        self.pv_lbl.setText('미리보기 만드는 중…')
        run_task(job, done=done, failed=failed, busy='캐릭터 미리보기')

    def show_preview(self):
        if not self.tmp:
            return
        g = self.pv_gender.currentData()
        cfg = GENDERS[g]
        base = os.path.join(self.tmp, *cfg['skeleton'].split('\\'))
        if not os.path.isfile(base):
            self.pv_lbl.setText(f'{cfg["label"]} 캐릭터는 이번 미리보기에 없습니다')
            self.view.set_model(None)
            return
        folder = os.path.dirname(base)
        want = self.anim.currentData()
        anim = None
        for f in sorted(os.listdir(folder)):
            stem = f.lower().removesuffix('.nwat')
            if f.lower().endswith('.nwat') and (stem == want or stem.endswith('_' + want)):
                anim = os.path.join(folder, f)
                break
        body = os.path.join(self.tmp, *cfg['body'].split('\\'))
        glb = os.path.join(self.tmp, f'preview_{g}_{want}.glb')

        def job():
            if not os.path.isfile(glb):
                convert.export_gltf([body], glb, skeleton=base, anims=[anim] if anim else [])
            return GltfModel(glb)

        def done(model):
            self.view.set_model(model, keep_camera=self.view.model is not None)
            self.view.playing = True
            self.pv_lbl.setText(f'{cfg["label"]} · {self.anim.currentText()}'
                                + ('' if anim else ' (이 동작 없음)'))
        run_task(job, done=done, busy='미리보기')

    # ------------------------------------------------------------------ apply
    def apply(self):
        if not self.src:
            return error('먼저 모델 파일을 고르세요')
        o = self.options()
        if not o['genders']:
            return error('여자/남자 중 하나 이상 고르세요')
        if not self.mw.require_project():
            return
        if self.svc.status() and not confirm('캐릭터 교체', '이 모드에 이미 캐릭터 교체가 있습니다. 새 모델로 바꿀까요?', '바꾸기'):
            return
        self.save_cfg()
        src = self.src

        def job(progress):
            return self.svc.build(src, o['genders'], o['height'], o['arm_angle'], o['leg_angle'], o['npcs'], o['hide'],
                                  o['turn'], o['blender'], progress=lambda d, t, m='': progress(d, t, m))

        def done(r):
            self.applied = True
            self.mw.refresh_header()
            self.refresh_status()
            g = r.get('genders', {})
            lines = [f"모드에 저장했습니다: 파일 {r['files']:,}개 ({r['bytes'] / 1e6:.1f} MB, {r['seconds']}초)"]
            for k, v in g.items():
                lines.append(f"{GENDERS[k]['label']}: 키 {v.get('height')}, 동작 {v.get('anims')}개, 숨긴 파츠 {v.get('hidden')}개")
            lines.append('\n게임에 넣으려면 모드 관리자에서 이 모드를 설치·적용하세요.')
            info('\n'.join(lines), '캐릭터 교체')
        run_task(job, done=done, busy='캐릭터 교체', progress=True)

    def remove(self):
        if not confirm('교체 되돌리기', '이 모드의 캐릭터 교체 파일을 모두 지울까요? (교체 전에 있던 파일은 되살립니다)',
                       '되돌리기', danger=True):
            return
        try:
            r = self.svc.remove()
        except StudioError as ex:
            return error(str(ex))
        self.mw.refresh_header()
        self.refresh_status()
        info(f"되돌렸습니다: 파일 {r['removed']}개 삭제, {r['restored']}개 복원", '캐릭터 교체')

    def done(self, r):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None
        try:
            self.save_cfg()
        except Exception:
            pass
        super().done(r)

    def closeEvent(self, e):
        self.done(0)
        super().closeEvent(e)
