"""Model / animation operations: glTF for the viewer and Blender, glTF import, animation speed."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid

from narew import convert
from narew.modfmt import check_rel_path, entry_key
from narew.nwmodel import guess_skeleton, load_model

from . import tables, textcodec
from .core import Studio, StudioError
from .mirror import get_mirror

CATEGORIES = [
    ('item\\look\\', '옷'), ('item\\hair\\', '머리'), ('item\\face\\', '얼굴'), ('item\\shoes\\', '신발'),
    ('item\\hands\\', '손'), ('item\\cap\\', '모자'), ('item\\accessory\\', '액세서리'), ('item\\back\\', '등 장식'),
    ('item\\tools\\', '도구'), ('item\\npc\\', 'NPC 장비'), ('item\\drop\\', '떨어진 아이템'), ('item\\', '아이템 기타'),
    ('npc\\', 'NPC·동물'), ('object\\', '오브젝트·가구'), ('effect\\', '이펙트'), ('sky\\', '하늘'),
    ('character\\', '캐릭터'), ('patch\\', '패치'),
]
_FRAME_RATE = re.compile(r'(<frame\b[^>]*?\bframerate\s*=\s*")(\d+(?:\.\d+)?)(")')


def category(name: str) -> str:
    k = entry_key(name)
    for prefix, label in CATEGORIES:
        if k.startswith(prefix):
            return label
    return '기타'


class ModelService:
    def __init__(self, st: Studio):
        self.st = st
        self.mirror = get_mirror(st)
        self.glb_dir = os.path.join(st.cache_dir, 'glb')
        os.makedirs(self.glb_dir, exist_ok=True)
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ listing
    def list_models(self) -> list[dict]:
        st = self.st
        g = st.need_game()
        out = {k: loc.entry.name for k, loc in g.index.items() if k.endswith('.nwmt')}
        for k, (n, _) in st.project_files().items():
            if k.endswith('.nwmt'):
                out[k] = n
        pf = st.project_files()
        return [{'name': n, 'category': category(n), 'modified': k in pf} for k, n in sorted(out.items())]

    def skeleton_for(self, model_paths: list[str]) -> str | None:
        for p in model_paths:
            m = load_model(p)
            if any(x.kind == 'skinmesh' for x in m.meshes):
                s = guess_skeleton(p, m.bone_count)
                if s:
                    return s
        return None

    def materialize(self, names, anims=(), skeleton=None):
        for n in names:
            check_rel_path(n)
        paths = [self.mirror.ensure_model(n) for n in names]
        skel = self.mirror.ensure(skeleton) if skeleton else self.skeleton_for(paths)
        anim_paths = []
        for a in anims:
            p = self.mirror.ensure(a)
            if p is None:
                raise StudioError(f'없는 애니메이션: {a}')
            anim_paths.append(p)
        return paths, skel, anim_paths

    def info(self, names: list[str]) -> dict:
        if not names:
            raise StudioError('모델을 고르세요')
        with self.lock:
            paths, skel, _ = self.materialize(names)
            models = []
            for n, p in zip(names, paths):
                m = load_model(p)
                models.append({'name': self.st.canonical_name(n), 'bones': m.bone_count,
                               'skinned': any(x.kind == 'skinmesh' for x in m.meshes),
                               'meshes': len(m.meshes), 'materials': len(m.materials),
                               'status': self.st.status_of(n)})
        skel_entry = self.mirror.entry_of(skel) if skel else None
        anims = {}
        if skel_entry:
            for a in self.mirror.siblings(skel_entry, '.nwat'):
                anims[entry_key(a)] = a
        for n in names:
            for a in self.mirror.siblings(n, '.nwat'):
                anims[entry_key(a)] = a
        return {'models': models, 'skeleton': skel_entry,
                'anims': [{'name': a, 'status': self.st.status_of(a)} for a in sorted(anims.values(), key=str.lower)]}

    # ------------------------------------------------------------------ glb
    def _key(self, names, anims, skel) -> str:
        parts = []
        for n in list(names) + list(anims) + ([skel] if skel else []):
            parts.append([entry_key(n), repr(self.mirror.signature(n))])
            if n.lower().endswith('.nwmt'):
                try:
                    for mt in tables.model_materials(self.st.read(n)):
                        for ref in mt['textures'].values():
                            t = tables.engine_texture_entry(n, ref)
                            if t:
                                parts.append([entry_key(t), repr(self.mirror.signature(t))])
                except Exception:
                    pass
        return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:20]

    def glb(self, names: list[str], anims: list[str] = (), skeleton: str | None = None) -> str:
        """Path of a cached .glb for the viewer (textures embedded)."""
        with self.lock:
            paths, skel, anim_paths = self.materialize(names, anims, skeleton)
            skel_entry = self.mirror.entry_of(skel) if skel else None
            out = os.path.join(self.glb_dir, self._key(names, anims, skel_entry) + '.glb')
            if not os.path.isfile(out):
                try:
                    convert.export_gltf(paths, out + '.tmp.glb', skeleton=skel, anims=anim_paths)
                except Exception as ex:
                    raise StudioError(f'모델 변환 실패: {ex}')
                os.replace(out + '.tmp.glb', out)
        return out

    def export(self, names, anims, skeleton, path) -> str:
        if not path.lower().endswith(('.glb', '.gltf')):
            path += '.glb'
        with self.lock:
            paths, skel, anim_paths = self.materialize(names, anims, skeleton)
            convert.export_gltf(paths, path, skeleton=skel, anims=anim_paths)
        return path

    def _tmpdir(self) -> str:
        d = os.path.join(self.st.cache_dir, 'import', uuid.uuid4().hex[:10])
        os.makedirs(d)
        return d

    def import_models(self, names: list[str], src: str) -> tuple[list[str], list[str]]:
        """glTF/GLB → .nwmt for every model of the scene found in the file (written to the project)."""
        st = self.st
        st.need_project()
        if not os.path.isfile(src):
            raise StudioError('glTF 파일을 찾을 수 없습니다')
        by_file = {os.path.basename(n.replace('\\', '/')).lower(): st.canonical_name(n) for n in names}
        d = self._tmpdir()
        saved, skipped = [], []
        try:
            with self.lock:
                paths, skel, _ = self.materialize(names)
                for n, p in zip(names, paths):
                    base = os.path.basename(p)
                    try:
                        written = convert.import_model(src, os.path.join(d, base), template=p, skeleton=skel,
                                                       model=base, write_textures=True)
                    except Exception as ex:
                        skipped.append(f'{base}: {ex}')
                        continue
                    target_dir = os.path.dirname(st.canonical_name(n))
                    for w in written:
                        fn = os.path.basename(w)
                        entry = by_file.get(fn.lower()) or (target_dir + '\\' + fn if target_dir else fn)
                        with open(w, 'rb') as f:
                            data = f.read()
                        if not fn.lower().endswith('.nwmt') and st.exists(entry) and st.read(entry) == data:
                            continue
                        st.write(entry, data)
                        saved.append(entry)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        if not saved:
            raise StudioError('가져온 모델이 없습니다. ' + '; '.join(skipped))
        return saved, skipped

    def import_new_model(self, src: str, target: str, template: str | None) -> list[str]:
        """glTF → a new .nwmt at target (textures beside it), optionally shaped like template."""
        st = self.st
        st.need_project()
        check_rel_path(target)
        if not target.lower().endswith('.nwmt'):
            raise StudioError('모델 파일 이름은 .nwmt로 끝나야 합니다')
        d = self._tmpdir()
        saved = []
        try:
            with self.lock:
                tmpl = self.mirror.ensure_model(template) if template else None
                written = convert.import_model(src, os.path.join(d, os.path.basename(target.replace('\\', '/'))),
                                               template=tmpl, write_textures=True)
            target_dir = os.path.dirname(target.replace('/', '\\'))
            for w in written:
                fn = os.path.basename(w)
                entry = target if fn.lower().endswith('.nwmt') else (target_dir + '\\' + fn if target_dir else fn)
                with open(w, 'rb') as f:
                    st.write(entry, f.read())
                saved.append(entry)
        except StudioError:
            raise
        except Exception as ex:
            raise StudioError(f'glTF를 모델로 바꿀 수 없습니다: {ex}')
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return saved

    def import_anim(self, target: str, src: str, skeleton: str | None, animation: str | None = None) -> int:
        st = self.st
        st.need_project()
        check_rel_path(target)
        if not target.lower().endswith('.nwat'):
            raise StudioError('애니메이션 파일 이름은 .nwat로 끝나야 합니다')
        if not os.path.isfile(src):
            raise StudioError('glTF 파일을 찾을 수 없습니다')
        d = self._tmpdir()
        try:
            with self.lock:
                skel = self.mirror.ensure(skeleton) if skeleton else None
                template = self.mirror.ensure(target)
                out = os.path.join(d, os.path.basename(target.replace('\\', '/')))
                written = convert.import_animation(src, out, skeleton=skel, animation=animation, template=template)
            if not written:
                raise StudioError('glTF에 애니메이션이 없습니다')
            with open(written[0], 'rb') as f:
                st.write(target, f.read())
            return len(written)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------ quick edits
    def anim_framerate(self, name: str, source: str = 'auto') -> float | None:
        text, _ = textcodec.decode(self.st.read(name, source))
        m = _FRAME_RATE.search(text)
        return float(m.group(2)) if m else None

    def set_anim_speed(self, name: str, speed: float) -> tuple[float, int]:
        """header frame framerate = milliseconds per key in the engine (docs/04 §4.2); relative to the original."""
        if not 0.1 <= speed <= 10:
            raise StudioError('속도는 0.1~10배')
        text, info = textcodec.decode(self.st.read(name, 'game'))
        m = _FRAME_RATE.search(text)
        if not m:
            raise StudioError('framerate 값을 찾을 수 없습니다')
        old = float(m.group(2))
        new = max(1, round(old / speed))
        text = text[:m.start(2)] + str(new) + text[m.end(2):]
        self.st.write(name, textcodec.encode(text, info))
        return old, new
