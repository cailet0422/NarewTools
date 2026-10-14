"""Format-neutral 3D scene used by the character swap (``narew.charswap``).

Every importer (FBX, glTF/GLB/VRM, OBJ, PMX, or anything Blender can open) produces a
:class:`Scene` in one space: **right-handed, +Y up, metres**, triangles counter-clockwise
seen from the front, UV origin top-left (glTF/D3D).  Bones carry their bind-pose world
matrix (4x4, column vectors: ``p_world = M @ p``), meshes are already in bind pose world
space and skin weights index ``Scene.bones``.

``load(path)`` picks the importer from the extension.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np


class SceneError(Exception):
    """Errors are Korean messages meant for the user."""


@dataclass
class SBone:
    name: str
    parent: int
    bind: np.ndarray                 # 4x4 world bind matrix (column vectors)

    @property
    def head(self) -> np.ndarray:
        return self.bind[:3, 3].copy()


@dataclass
class SMaterial:
    name: str
    color: tuple = (1.0, 1.0, 1.0, 1.0)
    image: bytes | None = None       # encoded image (png/jpg/tga/...)
    image_name: str | None = None
    double_sided: bool = False


@dataclass
class SMesh:
    name: str
    positions: np.ndarray            # (N, 3)
    normals: np.ndarray | None       # (N, 3)
    uvs: np.ndarray | None           # (N, 2)
    faces: np.ndarray                # (F, 3) int
    face_material: np.ndarray        # (F,) index into Scene.materials, -1 = none
    joints: np.ndarray | None = None     # (N, K) bone indices
    weights: np.ndarray | None = None    # (N, K)


@dataclass
class Scene:
    source: str = ''
    fmt: str = ''
    meshes: list[SMesh] = field(default_factory=list)
    bones: list[SBone] = field(default_factory=list)
    materials: list[SMaterial] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    humanoid: dict = field(default_factory=dict)     # slot -> bone index from the file (VRM)

    # ---------------------------------------------------------------- helpers
    def children(self, i: int) -> list[int]:
        return [j for j, b in enumerate(self.bones) if b.parent == i]

    def descendants(self, i: int) -> list[int]:
        out, stack = [], [i]
        while stack:
            k = stack.pop()
            for c in self.children(k):
                out.append(c)
                stack.append(c)
        return out

    def ancestors(self, i: int) -> list[int]:
        out = []
        p = self.bones[i].parent
        while p >= 0:
            out.append(p)
            p = self.bones[p].parent
        return out

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        pts = np.concatenate([m.positions for m in self.meshes]) if self.meshes else np.zeros((1, 3))
        return pts.min(axis=0), pts.max(axis=0)

    def counts(self) -> dict:
        return {'meshes': len(self.meshes), 'vertices': int(sum(len(m.positions) for m in self.meshes)),
                'triangles': int(sum(len(m.faces) for m in self.meshes)), 'bones': len(self.bones),
                'materials': len(self.materials),
                'skinned': any(m.weights is not None for m in self.meshes)}

    def transform(self, m4: np.ndarray) -> None:
        """Apply a 4x4 to everything (bones, positions, normals); flips winding if mirrored."""
        r = m4[:3, :3]
        nm = np.linalg.inv(r).T
        for b in self.bones:
            b.bind = m4 @ b.bind
        for m in self.meshes:
            m.positions = (r @ m.positions.T).T + m4[:3, 3]
            if m.normals is not None:
                n = (nm @ m.normals.T).T
                ln = np.linalg.norm(n, axis=1, keepdims=True)
                m.normals = n / np.where(ln > 1e-12, ln, 1.0)
            if np.linalg.det(r) < 0:
                m.faces = m.faces[:, [0, 2, 1]]


# --------------------------------------------------------------------------- images

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.tga', '.bmp', '.dds', '.tif', '.tiff', '.psd', '.gif', '.webp')


def read_image_file(name: str, base: str) -> tuple[bytes | None, str | None]:
    """Find a texture referenced by a model file: as given, next to the model, or by file name
    in the model folder and its sub folders (exporters often store the author's absolute path)."""
    name = (name or '').strip().strip('"')
    if not name:
        return None, None
    fn = os.path.basename(name.replace('\\', '/'))
    cands = [name, os.path.join(base, name), os.path.join(base, fn)]
    for c in cands:
        if c and os.path.isfile(c):
            with open(c, 'rb') as f:
                return f.read(), os.path.basename(c)
    low = fn.lower()
    stem = os.path.splitext(low)[0]
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f.lower() == low:
                with open(os.path.join(root, f), 'rb') as fh:
                    return fh.read(), f
        for f in files:      # same name, other image type (exporters rename .psd -> .png)
            if os.path.splitext(f.lower())[0] == stem and f.lower().endswith(IMAGE_EXTS):
                with open(os.path.join(root, f), 'rb') as fh:
                    return fh.read(), f
        if root.count(os.sep) - base.count(os.sep) >= 2:
            break
    return None, fn


def lone_image(base: str) -> tuple[bytes | None, str | None]:
    """The only image file in a model folder (many downloads ship one atlas + the model)."""
    imgs = [f for f in sorted(os.listdir(base)) if f.lower().endswith(IMAGE_EXTS)]
    if len(imgs) == 1:
        with open(os.path.join(base, imgs[0]), 'rb') as f:
            return f.read(), imgs[0]
    return None, None


# --------------------------------------------------------------------------- loading

NATIVE = {'.fbx': 'FBX', '.glb': 'glTF', '.gltf': 'glTF', '.vrm': 'VRM', '.obj': 'OBJ', '.pmx': 'PMX'}
VIA_BLENDER = {'.dae': 'Collada', '.3ds': '3DS', '.blend': 'Blender', '.usd': 'USD', '.usda': 'USD',
               '.usdc': 'USD', '.usdz': 'USD', '.abc': 'Alembic', '.ply': 'PLY', '.stl': 'STL',
               '.x3d': 'X3D', '.wrl': 'VRML'}
SUPPORTED = {**NATIVE, **VIA_BLENDER}


def supported_extensions() -> list[str]:
    return sorted(SUPPORTED)


def load(path: str, blender: str | None = None) -> Scene:
    if not os.path.isfile(path):
        raise SceneError(f'파일이 없습니다: {path}')
    ext = os.path.splitext(path)[1].lower()
    if ext == '.fbx':
        from . import fbx
        try:
            sc = fbx.load_scene(path)
        except SceneError as e:
            if find_blender(blender):            # FBX 6 ASCII and other odd files
                sc = load_via_blender(path, blender)
                sc.notes.append(f'내장 FBX 읽기 실패({e}) → Blender로 변환')
            else:
                raise
    elif ext in ('.glb', '.gltf', '.vrm'):
        from . import scene_gltf
        sc = scene_gltf.load_scene(path)
    elif ext == '.obj':
        from . import scene_obj
        sc = scene_obj.load_scene(path)
    elif ext == '.pmx':
        from . import scene_pmx
        sc = scene_pmx.load_scene(path)
    elif ext in VIA_BLENDER:
        sc = load_via_blender(path, blender)
    else:
        raise SceneError(f'지원하지 않는 형식입니다: {ext} (지원: {", ".join(supported_extensions())})')
    # textures missing in the file: use the lone image next to the model
    base = os.path.dirname(os.path.abspath(path))
    if any(m.image is None for m in sc.materials) or not sc.materials:
        data, name = lone_image(base)
        if data is not None:
            if not sc.materials:
                sc.materials.append(SMaterial('material', image=data, image_name=name))
                for m in sc.meshes:
                    m.face_material = np.zeros(len(m.faces), dtype=np.int64)
                sc.notes.append(f'재질이 없어 폴더의 그림 {name} 사용')
            for m in sc.materials:
                if m.image is None:
                    m.image, m.image_name = data, name
                    sc.notes.append(f'재질 {m.name}: 텍스처를 찾지 못해 폴더의 그림 {name} 사용')
    return sc


# --------------------------------------------------------------------------- Blender

def find_blender(hint: str | None = None) -> str | None:
    """blender.exe: the given path, NAREW_BLENDER, PATH, the usual install folders."""
    cands = []
    if hint:
        cands.append(hint if not os.path.isdir(hint) else os.path.join(hint, 'blender.exe'))
    env = os.environ.get('NAREW_BLENDER')
    if env:
        cands.append(env if not os.path.isdir(env) else os.path.join(env, 'blender.exe'))
    w = shutil.which('blender')
    if w:
        cands.append(w)
    roots = [os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', ''),
             os.path.expandvars(r'%LOCALAPPDATA%\Programs')]
    for r in roots:
        d = os.path.join(r, 'Blender Foundation')
        if r and os.path.isdir(d):
            for sub in sorted(os.listdir(d), reverse=True):
                cands.append(os.path.join(d, sub, 'blender.exe'))
    steam = r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'
    cands.append(steam)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


_BLENDER_SCRIPT = r'''
import bpy, sys
argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
ext = src.lower().rsplit(".", 1)[-1]
if ext == "blend":
    bpy.ops.wm.open_mainfile(filepath=src)
elif ext == "fbx":
    bpy.ops.import_scene.fbx(filepath=src)
elif ext == "dae":
    bpy.ops.wm.collada_import(filepath=src)
elif ext == "3ds":
    try:
        bpy.ops.import_scene.max3ds(filepath=src)
    except Exception:
        bpy.ops.import_scene.autodesk_3ds(filepath=src)
elif ext in ("usd", "usda", "usdc", "usdz"):
    bpy.ops.wm.usd_import(filepath=src)
elif ext == "abc":
    bpy.ops.wm.alembic_import(filepath=src)
elif ext == "ply":
    try:
        bpy.ops.wm.ply_import(filepath=src)
    except Exception:
        bpy.ops.import_mesh.ply(filepath=src)
elif ext == "stl":
    try:
        bpy.ops.wm.stl_import(filepath=src)
    except Exception:
        bpy.ops.import_mesh.stl(filepath=src)
elif ext in ("x3d", "wrl"):
    bpy.ops.import_scene.x3d(filepath=src)
else:
    raise SystemExit("unsupported " + ext)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_animations=False,
                          export_skins=True, export_yup=True)
'''


def load_via_blender(path: str, blender: str | None = None) -> Scene:
    exe = find_blender(blender)
    ext = os.path.splitext(path)[1].lower()
    if not exe:
        raise SceneError(f'{SUPPORTED.get(ext, ext)} 형식은 Blender가 있어야 읽을 수 있습니다. '
                         'Blender를 설치하거나 blender.exe 위치를 지정하세요 '
                         '(FBX·glTF·GLB·VRM·OBJ·PMX는 Blender 없이 읽습니다).')
    tmp = tempfile.mkdtemp(prefix='narew_blender_')
    try:
        script = os.path.join(tmp, 'convert.py')
        out = os.path.join(tmp, 'model.glb')
        with open(script, 'w', encoding='utf-8') as f:
            f.write(_BLENDER_SCRIPT)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        r = subprocess.run([exe, '--background', '--factory-startup', '--python', script, '--',
                            os.path.abspath(path), out], capture_output=True, text=True,
                           timeout=600, creationflags=flags)
        if not os.path.isfile(out):
            tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
            raise SceneError('Blender 변환에 실패했습니다: ' + ' / '.join(tail))
        from . import scene_gltf
        sc = scene_gltf.load_scene(out)
        sc.source = path
        sc.fmt = f'{SUPPORTED.get(ext, ext)} (Blender {os.path.basename(os.path.dirname(exe))})'
        return sc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
