"""Character swap: put a model file (FBX, glTF/GLB/VRM, OBJ, PMX, or anything Blender opens) in place
of the Islet player character, for both genders, with the game's own animations.

What is written into the open mod project (all generated from the *game's original* files, so
running it again with another model starts clean):

* ``Character\\F1\\f1.nwbt`` / ``Character\\M1\\m1.nwbt``: the skeletons with the model's joint positions
* ``Item\\Default\\f1_body01.nwmt`` / ``m1_body01.nwmt``: the model, skinned to those skeletons
* every animation in ``Character\\F1`` / ``Character\\M1``: bone lengths + root motion fitted
* the model's textures (``Item\\Default\\cs_<name>.png`` + black mask/specular maps)
* every other part worn on the body (face, hair, clothes, shoes, gloves, hats, back, accessories)
  shrunk to an invisible triangle - the new model already has its own face, hair and clothes
* the same for the human NPCs in ``npc\\human`` (their skeletons, models and animations), because
  they wear the same parts

``charswap.json`` in the project records what was written; files the project already had are
kept in ``.charswap_backup`` and come back with :meth:`CharSwapService.remove`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

from narew import charswap, scene3d
from narew.modfmt import entry_key
from narew.scene3d import SceneError

from .core import Studio, StudioError

GENDERS = {
    'f1': {'label': '여자', 'skeleton': 'Character\\F1\\f1.nwbt', 'body': 'Item\\Default\\f1_body01.nwmt',
           'ref': ('Item\\Default\\f1_body01.nwmt', 'Item\\Default\\f1_face01.nwmt', 'Item\\Default\\f1_hair01.nwmt')},
    'm1': {'label': '남자', 'skeleton': 'Character\\M1\\m1.nwbt', 'body': 'Item\\Default\\m1_body01.nwmt',
           'ref': ('Item\\Default\\m1_body01.nwmt', 'Item\\Default\\m1_face01.nwmt', 'Item\\Default\\m1_hair01.nwmt')},
}
NPC_DIR = 'npc\\human\\'
WEAR_DIRS = ('item\\body\\', 'item\\collabo\\', 'item\\default\\', 'item\\face\\', 'item\\hair\\',
             'item\\hands\\', 'item\\look\\', 'item\\shoes\\', 'item\\accessory\\', 'item\\back\\', 'item\\cap\\')
MANIFEST = 'charswap.json'
BACKUP_DIR = '.charswap_backup'
_BONES = re.compile(rb'<header[^>]*\sbone="(\d+)"')
_MAXY = re.compile(rb'<bound_box[^>]*\smaxy="([-0-9.eE+]+)"')


def formats_text() -> str:
    native = sorted(e for e in scene3d.NATIVE)
    blender = sorted(e for e in scene3d.VIA_BLENDER)
    return f'바로 읽기: {" ".join(native)} / Blender 필요: {" ".join(blender)}'


def _bone_count(data: bytes) -> int:
    m = _BONES.search(data[:1024])
    return int(m.group(1)) if m else -1


def _slug(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    s = re.sub(r'[^a-z0-9]+', '_', stem).strip('_')[:28]
    return 'cs_' + (s or 'model')


class CharSwapService:
    def __init__(self, st: Studio):
        self.st = st

    # ------------------------------------------------------------------ catalogue
    def _game_keys(self) -> dict[str, str]:
        return {k: loc.entry.name for k, loc in self.st.need_game().index.items()}

    def plan(self, genders=('f1', 'm1'), npcs: bool = True, hide: bool = True) -> dict:
        """Which game files a swap replaces (entry names), per gender."""
        keys = self._game_keys()
        st = self.st
        out = {}
        for g in genders:
            if g not in GENDERS:
                raise StudioError(f'성별은 f1(여자) 또는 m1(남자)입니다: {g}')
            cfg = GENDERS[g]
            skel_key = entry_key(cfg['skeleton'])
            if skel_key not in keys:
                raise StudioError(f'게임에 {cfg["skeleton"]}이(가) 없습니다')
            nb = _bone_count(st.read(cfg['skeleton'], 'game'))
            folder = os.path.dirname(skel_key) + '\\'
            anims = sorted(n for k, n in keys.items() if k.endswith('.nwat') and os.path.dirname(k) + '\\' == folder)
            hidden = []
            if hide:
                body_key = entry_key(cfg['body'])
                for k, n in sorted(keys.items()):
                    if not k.endswith('.nwmt') or k == body_key or not k.startswith(WEAR_DIRS):
                        continue
                    if os.path.basename(k).startswith(g + '_'):
                        hidden.append(n)
            p = {'label': cfg['label'], 'skeleton': keys[skel_key], 'bones': nb, 'body': keys.get(entry_key(cfg['body']), cfg['body']),
                 'anims': anims, 'hide': hidden, 'npc': None}
            if npcs:
                nskel = entry_key(NPC_DIR + g + '.nwbt')
                if nskel in keys:
                    models, nhide, nanims = [], [], []
                    for k, n in sorted(keys.items()):
                        if not k.startswith(NPC_DIR) or os.path.dirname(k) + '\\' != NPC_DIR:
                            continue
                        b = os.path.basename(k)
                        if k.endswith('.nwmt'):
                            if _bone_count(st.read(n, 'game')) != nb:
                                continue
                            if b.startswith('human_' + g) or b == g + '_body01.nwmt':
                                models.append(n)
                            elif b.startswith(g + '_'):
                                nhide.append(n)
                        elif k.endswith('.nwat') and _bone_count(st.read(n, 'game')) == nb:
                            nanims.append(n)
                    p['npc'] = {'skeleton': keys[nskel], 'models': models, 'hide': nhide if hide else [],
                                'anims': nanims}
            out[g] = p
        return out

    def ref_height(self, g: str) -> float:
        """Height of the game's default character (body + face + hair)."""
        best = 0.0
        for n in GENDERS[g]['ref']:
            try:
                data = self.st.read(n, 'game')
            except Exception:
                continue
            for m in _MAXY.finditer(data):
                best = max(best, float(m.group(1)))
        return round(best, 3) if best > 0.5 else 1.85

    # ------------------------------------------------------------------ source
    def inspect(self, src: str, blender: str | None = None, turn: float = 0.0) -> dict:
        try:
            sc = scene3d.load(src, blender)
        except SceneError as ex:
            raise StudioError(str(ex))
        rig = charswap.detect_rig(sc)
        lo, hi = sc.bounds()
        out = {'source': os.path.abspath(src), 'format': sc.fmt, **sc.counts(),
               'size': [round(float(x), 4) for x in (hi - lo)], 'notes': list(sc.notes),
               'materials': [{'name': m.name, 'texture': m.image_name, 'has_image': m.image is not None}
                             for m in sc.materials],
               'humanoid': rig is not None}
        if rig is not None:
            out['mapping'] = {k: sc.bones[v].name for k, v in sorted(rig.slots.items()) if v is not None}
            out['spine'] = [sc.bones[b].name for b in rig.spine]
            out['fingers'] = {s: sorted(f) for s, f in rig.fingers.items()}
        else:
            out['notes'].append('사람 뼈대를 찾지 못했습니다: 모델이 골반에 통째로 붙어 걷기·팔 동작이 보이지 않습니다. '
                                '뼈대(아마추어)가 있는 FBX/glTF/VRM/PMX를 쓰세요.')
        return out

    # ------------------------------------------------------------------ build
    def build(self, src: str, genders=('f1', 'm1'), height: float | None = None, arm_angle: float = 12.0,
              leg_angle: float | None = None, npcs: bool = True, hide: bool = True, turn: float = 0.0,
              blender: str | None = None, out_dir: str | None = None, progress=None) -> dict:
        """Generate everything.  ``out_dir`` = write the files there (preview / dry run) instead of the
        project.  ``progress(done, total, text)`` is called along the way."""
        st = self.st
        st.need_game()
        if out_dir is None:
            st.need_project()
        genders = [g.lower() for g in (genders or ())]
        if not genders:
            raise StudioError('바꿀 캐릭터(f1 여자 / m1 남자)를 하나 이상 고르세요')
        t0 = time.time()
        plan = self.plan(genders, npcs, hide)
        try:
            base_scene = scene3d.load(src, blender)
        except SceneError as ex:
            raise StudioError(str(ex))
        prefix = _slug(src)
        total = sum(1 + len(p['anims']) + len(p['hide']) + (
            (len(p['npc']['models']) + len(p['npc']['anims']) + len(p['npc']['hide'])) if p['npc'] else 0)
            for p in plan.values())
        done = 0

        def tick(text):
            nonlocal done
            done += 1
            if progress:
                progress(done, total, text)

        files: dict[str, bytes] = {}
        report = {'source': os.path.abspath(src), 'format': base_scene.fmt, 'prefix': prefix, 'genders': {}}
        for g, p in plan.items():
            h = float(height) if height else self.ref_height(g)
            targets = [(p['skeleton'], [(p['body'], GENDERS[g]['body'])], p['anims'], 'Item\\Default\\')]
            if p['npc']:
                targets.append((p['npc']['skeleton'], [(m, m) for m in p['npc']['models']], p['npc']['anims'], 'npc\\human\\'))
            grep = {'height': h, 'anims': 0, 'hidden': 0}
            for ti, (skel_name, models, anims, tex_dir) in enumerate(targets):
                sc = copy.deepcopy(base_scene)
                try:
                    sc, rig, srep = charswap.prepare_source(src, h, turn=turn, scene=sc)
                    tgt = charswap.Target(st.read(skel_name, 'game'))
                except SceneError as ex:
                    raise StudioError(str(ex))
                if rig is not None:
                    la = leg_angle if leg_angle is not None else charswap.auto_leg_angle(sc, rig, tgt)
                    charswap.repose(sc, rig, tgt, arm_angle=arm_angle, leg_angle=la)
                    srep['leg_angle'] = round(la, 2)
                srep['arm_angle'] = arm_angle
                ch = None
                for mi, (model_name, template_name) in enumerate(models):
                    ch = charswap.build(sc, rig, tgt, st.read(template_name, 'game'), prefix, g)
                    files[model_name] = ch.body
                    tick(model_name)
                if ch is None:
                    continue
                files[skel_name] = ch.skeleton
                for tn, data in ch.textures.items():
                    files[tex_dir + tn] = data
                for a in anims:
                    files[a] = charswap.retarget_animation(st.read(a, 'game'), ch.plan)
                    grep['anims'] += 1
                    tick(a)
                if ti == 0:
                    grep.update({k: srep[k] for k in ('scale', 'leg_angle', 'arm_angle') if k in srep})
                    grep.update(ch.report)
                    report.setdefault('mapping', srep.get('mapping'))
                    report.setdefault('notes', srep.get('notes'))
                    report.setdefault('source_counts', srep.get('source'))
            hide_list = list(p['hide']) + (list(p['npc']['hide']) if p['npc'] else [])
            for n in hide_list:
                try:
                    files[n] = charswap.collapse_model(st.read(n, 'game'))
                    grep['hidden'] += 1
                except Exception as ex:          # a broken shipped file: leave it alone
                    report.setdefault('skipped', []).append(f'{n}: {ex}')
                tick(n)
            if p['npc']:
                grep['npc_models'] = list(p['npc']['models'])
            report['genders'][g] = grep
        report['files'] = len(files)
        report['bytes'] = sum(len(b) for b in files.values())
        report['seconds'] = round(time.time() - t0, 1)
        if out_dir is not None:
            for n, data in files.items():
                p = os.path.join(out_dir, *n.replace('/', '\\').split('\\'))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, 'wb') as f:
                    f.write(data)
            report['written_to'] = out_dir
            return report
        self._write_project(files, report, src, dict(genders=genders, height=height, arm_angle=arm_angle,
                                                      leg_angle=leg_angle, npcs=npcs, hide=hide, turn=turn))
        return report

    # ------------------------------------------------------------------ project bookkeeping
    def _manifest_path(self) -> str:
        return os.path.join(self.st.need_project().root, MANIFEST)

    def status(self) -> dict | None:
        if not self.st.project:
            return None
        p = self._manifest_path()
        if not os.path.isfile(p):
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def _write_project(self, files: dict, report: dict, src: str, options: dict) -> None:
        proj = self.st.need_project()
        old = self.status()
        if old:
            self.remove()
        backup_root = os.path.join(proj.root, BACKUP_DIR)
        pf = self.st.project_files()
        backups = []
        for n in files:
            hit = pf.get(entry_key(n))
            if hit:
                dst = os.path.join(backup_root, *hit[0].split('\\'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(hit[1], dst)
                backups.append(hit[0])
        written = []
        for n, data in files.items():
            proj.put(self.st.canonical_name(n), data)
            written.append(self.st.canonical_name(n))
        self.st.invalidate()
        h = hashlib.sha1()
        with open(src, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        man = {'source': os.path.abspath(src), 'sha1': h.hexdigest(), 'time': time.strftime('%Y-%m-%d %H:%M:%S'),
               'options': options, 'written': sorted(written, key=str.lower), 'backups': backups,
               'report': {k: v for k, v in report.items() if k != 'mapping'}}
        with open(self._manifest_path(), 'w', encoding='utf-8') as f:
            json.dump(man, f, ensure_ascii=False, indent=1)

    def remove(self) -> dict:
        """Undo the swap: delete what it wrote, put back files the project had before."""
        man = self.status()
        if not man:
            raise StudioError('이 프로젝트에는 캐릭터 교체가 없습니다')
        proj = self.st.need_project()
        removed = 0
        for n in man.get('written', []):
            if proj.remove(n):
                removed += 1
        backup_root = os.path.join(proj.root, BACKUP_DIR)
        restored = 0
        for n in man.get('backups', []):
            p = os.path.join(backup_root, *n.split('\\'))
            if os.path.isfile(p):
                with open(p, 'rb') as f:
                    proj.put(n, f.read())
                restored += 1
        shutil.rmtree(backup_root, ignore_errors=True)
        os.remove(self._manifest_path())
        self.st.invalidate()
        return {'removed': removed, 'restored': restored}


def preview_dir() -> str:
    return tempfile.mkdtemp(prefix='narew_charswap_')
