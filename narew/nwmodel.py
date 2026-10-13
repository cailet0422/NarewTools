"""In-memory representation of NarewEngine2 .nwmt / .nwbt / .nwat files.

Only the parts needed for geometry/skeleton/animation conversion are decoded into Python
objects. Every other top-level section (helpers, physics, lights, special bones, effects, ...)
is kept as raw XML so a model can be written back without losing data.

Coordinate conventions (engine, see docs/04-model-formats.md):
  * left-handed, +Y up, characters face -Z, row vectors (``v' = v * M``)
  * ``<transform>`` rows are a row-major 4x4 with the translation in row 3
  * quaternions are stored ``x y z w``
"""
from __future__ import annotations

import copy
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .nwxml import (detect_exp3, detect_indent, fmt_g, fmt_vec, floats, ival, parse_bytes,
                    parse_file, read_tm,
                    serialize, toplevel_spans)

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


# --------------------------------------------------------------------------- models

@dataclass
class Material:
    id: int
    name: str
    element: ET.Element          # original <material> element (written back verbatim)

    def map(self, kind: str) -> str:
        e = self.element.find(kind)
        return (e.text or '').strip() if e is not None else ''

    def set_map(self, kind: str, value: str) -> None:
        e = self.element.find(kind)
        if e is None:
            e = ET.SubElement(self.element, kind)
        e.text = value

    def color(self, which: str) -> str | None:
        c = self.element.find('color')
        return c.get(which) if c is not None else None

    @property
    def shader(self) -> str:
        s = self.element.find('shader')
        return s.get('file', '') if s is not None else ''


@dataclass
class Mesh:
    kind: str                                   # 'mesh' | 'skinmesh'
    id: int
    name: str
    material: int = -1
    parent_id: int = -1
    parent_name: str = 'Scene Root'
    transform: list = field(default_factory=lambda: copy.deepcopy(IDENTITY))
    bound_box: list | None = None               # [minx, miny, minz, maxx, maxy, maxz]
    use_bone: int | None = None
    positions: list = field(default_factory=list)      # [(x, y, z)]
    normals: list | None = None
    tangents: list | None = None                       # [(x, y, z, w)]
    uvs: list | None = None                            # [(u, v)]
    skin: list | None = None                           # per vertex [(bone, weight)] * 4
    faces: list = field(default_factory=list)          # [(a, b, c)]
    attrs: dict = field(default_factory=dict)          # extra attributes on the element
    index_attrs: dict = field(default_factory=dict)    # attributes on <index> besides count
    list_order: list = field(default_factory=list)     # order of the <vertex> sub-lists
    bbox_from_file: bool = True                        # bound_box still the file's own value
    write_bbox: bool = True                            # a few skinmeshes have no <bound_box>
    uv_width: int = 2                                  # 44 files store "u " without v (exporter bug)


@dataclass
class Model:
    path: str | None
    header: ET.Element | None
    materials: list[Material]
    meshes: list[Mesh]
    sections: list[tuple[str, ET.Element | None]]       # top-level order; None = decoded part
    raw: dict = field(default_factory=dict)             # tag -> exact source text (passthrough)
    indent: str = '    '
    list_counts: dict = field(default_factory=dict)     # element counts when the file was read
    exp3: bool = False                                  # %g with 3-digit exponents (older CRT)

    @property
    def bone_count(self) -> int:
        return ival(self.header, 'bone') if self.header is not None else 0

    def material_by_id(self, mid: int) -> Material | None:
        for m in self.materials:
            if m.id == mid:
                return m
        return None

    def special_bones(self) -> list['Bone']:
        lst = self.section('special_bone_list')
        return [_read_bone(b) for b in lst] if lst is not None else []

    def section(self, tag: str) -> ET.Element | None:
        for t, e in self.sections:
            if t == tag:
                return e
        return None


def _vec_list(parent: ET.Element | None, child: str, width: int):
    if parent is None:
        return None
    items = list(parent)
    out = [None] * len(items)
    for pos, it in enumerate(items):
        n = it.get('n')
        idx = int(n) if n is not None else pos
        vals = floats(it.get('value'))
        if len(vals) < width:                   # e.g. 44 files have "u " without v
            vals = vals + [0.0] * (width - len(vals))
        if idx >= len(out):
            out.extend([None] * (idx + 1 - len(out)))
        out[idx] = tuple(vals[:width])
    return out


def _read_mesh(kind: str, e: ET.Element) -> Mesh:
    m = Mesh(kind=kind, id=ival(e, 'id'), name=e.get('name', ''),
             material=ival(e, 'material', -1))
    known = {'id', 'name', 'material', 'use_bone'}
    m.attrs = {k: v for k, v in e.attrib.items() if k not in known}
    if e.get('use_bone') is not None:
        m.use_bone = ival(e, 'use_bone')
    p = e.find('parent')
    if p is not None:
        m.parent_id = ival(p, 'id', -1)
        m.parent_name = p.get('name', '')
    tm = read_tm(e.find('transform'))
    if tm is not None:
        m.transform = tm
    bb = e.find('bound_box')
    if bb is not None:
        m.bound_box = [float(bb.get(k, 0)) for k in ('minx', 'miny', 'minz', 'maxx', 'maxy', 'maxz')]
    else:
        m.write_bbox = False
    v = e.find('vertex')
    nv = ival(v, 'count') if v is not None else 0
    if v is not None:
        m.list_order = [c.tag for c in v]
        m.positions = _vec_list(v.find('point_list'), 'point', 3) or []
        m.normals = _vec_list(v.find('normal_list'), 'normal', 3)
        m.tangents = _vec_list(v.find('tangent_list'), 'tangent', 4)
        m.uvs = _vec_list(v.find('uv_list'), 'uv', 2)
        ul = v.find('uv_list')
        if ul is not None and len(ul) and all(len((u.get('value') or '').split()) == 1 for u in ul):
            m.uv_width = 1
        sl = v.find('skin_list')
        if sl is not None:
            skin = [[(0, 0.0)] * 4 for _ in range(nv)]
            for s in sl:
                vi, n = ival(s, 'vert'), ival(s, 'n')
                if 0 <= vi < nv and 0 <= n < 4:
                    skin[vi][n] = (ival(s, 'bone'), float(s.get('weight', 0)))
            m.skin = skin
    if len(m.positions) < nv:
        m.positions.extend([(0.0, 0.0, 0.0)] * (nv - len(m.positions)))
    for attr in ('normals', 'tangents', 'uvs'):
        lst = getattr(m, attr)
        if lst is not None:
            w = {'normals': 3, 'tangents': 4, 'uvs': 2}[attr]
            setattr(m, attr, [x if x is not None else (0.0,) * w for x in lst[:len(m.positions)]]
                    + [(0.0,) * w] * max(0, len(m.positions) - len(lst)))
    idx = e.find('index')
    if idx is not None:
        m.index_attrs = {k: v for k, v in idx.attrib.items() if k != 'count'}
        faces = [None] * len(idx)
        for pos, f in enumerate(idx):
            n = f.get('n')
            i = int(n) if n is not None else pos
            if i >= len(faces):
                faces.extend([None] * (i + 1 - len(faces)))
            faces[i] = (ival(f, 'a'), ival(f, 'b'), ival(f, 'c'))
        m.faces = [f for f in faces if f is not None]
    return m


def load_model(path: str) -> Model:
    with open(path, 'rb') as f:
        data = f.read()
    root = parse_bytes(data)
    header = root.find('header')
    materials: list[Material] = []
    meshes: list[Mesh] = []
    sections: list[tuple[str, ET.Element | None]] = []
    for c in root:
        if c.tag == 'material_list':
            for me in c:
                materials.append(Material(ival(me, 'id'), me.get('name', ''), me))
            sections.append((c.tag, None))
        elif c.tag in ('mesh_list', 'skinmesh_list'):
            kind = c.tag[:-5]
            for me in c:
                meshes.append(_read_mesh(kind, me))
            sections.append((c.tag, None))
        elif c.tag == 'header':
            sections.append((c.tag, None))
        elif c.tag == 'bound_box':
            sections.append((c.tag, None))
        else:
            sections.append((c.tag, c))
    raw = {}
    spans = toplevel_spans(data)
    tags = [t for t, _ in spans]
    if tags == [c.tag for c in root]:
        raw = {t: text for t, text in spans if tags.count(t) == 1}
    mdl = Model(path, header, materials, meshes, sections, raw, detect_indent(data))
    mdl.exp3 = detect_exp3(data)
    mdl.list_counts = {'material': len(materials),
                       'mesh': sum(1 for m in meshes if m.kind == 'mesh'),
                       'skinmesh': sum(1 for m in meshes if m.kind == 'skinmesh')}
    for c in root:      # the count attribute does not always match the list (39 mesh_lists)
        if c.tag in ('material_list', 'mesh_list', 'skinmesh_list') and c.get('count') is not None:
            mdl.list_counts['attr:' + c.tag[:-5]] = c.get('count')
    return mdl


def _list_count(model: Model, key: str, n: int) -> str:
    if model.list_counts.get(key) == n and ('attr:' + key) in model.list_counts:
        return model.list_counts['attr:' + key]
    return str(n)


def _same_elements(raw_text: str, e: ET.Element) -> bool:
    try:
        orig = parse_bytes(raw_text.encode('cp949', errors='replace'))[0]
    except ET.ParseError:
        return False
    return serialize([orig]) == serialize([e])


# --------------------------------------------------------------------------- writing

def _tm_element(tm) -> ET.Element:
    t = ET.Element('transform')
    for r in range(4):
        ET.SubElement(t, 'tm', {'row': str(r), 'value': fmt_vec(tm[r], 6)})
    return t


def _bbox_attrs(bb, exp3: bool = False) -> dict:
    keys = ('minx', 'miny', 'minz', 'maxx', 'maxy', 'maxz')
    return {k: fmt_g(v, exp3) for k, v in zip(keys, bb)}


def mesh_bbox(m: Mesh) -> list:
    if not m.positions:
        return [0.0] * 6
    xs, ys, zs = zip(*m.positions)
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


def transform_bbox(bb, tm) -> list:
    pts = [(x, y, z) for x in (bb[0], bb[3]) for y in (bb[1], bb[4]) for z in (bb[2], bb[5])]
    out = [mat_point(tm, p) for p in pts]
    xs, ys, zs = zip(*out)
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


def mat_point(tm, p):
    x, y, z = p
    return tuple(x * tm[0][c] + y * tm[1][c] + z * tm[2][c] + tm[3][c] for c in range(3))


def _mesh_element(m: Mesh, exp3: bool = False) -> ET.Element:
    attrs = {'id': str(m.id), 'name': m.name}
    if m.kind == 'skinmesh':
        attrs['use_bone'] = str(m.use_bone if m.use_bone is not None else used_bone_count(m))
    attrs['material'] = str(m.material)
    attrs.update(m.attrs)
    e = ET.Element(m.kind, attrs)
    ET.SubElement(e, 'parent', {'id': str(m.parent_id), 'name': m.parent_name})
    e.append(_tm_element(m.transform))
    if m.write_bbox:
        ET.SubElement(e, 'bound_box', _bbox_attrs(m.bound_box or mesh_bbox(m), exp3))
    v = ET.SubElement(e, 'vertex', {'count': str(len(m.positions))})
    order = m.list_order or (['point_list', 'skin_list', 'normal_list', 'tangent_list', 'uv_list']
                             if m.kind == 'skinmesh' else
                             ['point_list', 'normal_list', 'tangent_list', 'uv_list'])
    for tag in order:
        if tag == 'point_list':
            lst = ET.SubElement(v, tag)
            for i, p in enumerate(m.positions):
                ET.SubElement(lst, 'point', {'n': str(i), 'value': fmt_vec(p)})
        elif tag == 'skin_list' and m.skin is not None:
            lst = ET.SubElement(v, tag)
            for i, infl in enumerate(m.skin):
                for n, (b, w) in enumerate(infl):
                    ET.SubElement(lst, 'skin', {'vert': str(i), 'n': str(n), 'bone': str(b),
                                                'weight': fmt_g(w, exp3)})
        elif tag == 'normal_list' and m.normals is not None:
            lst = ET.SubElement(v, tag)
            for i, p in enumerate(m.normals):
                ET.SubElement(lst, 'normal', {'n': str(i), 'value': fmt_vec(p)})
        elif tag == 'tangent_list' and m.tangents is not None:
            lst = ET.SubElement(v, tag)
            for i, p in enumerate(m.tangents):
                ET.SubElement(lst, 'tangent', {'n': str(i), 'value': fmt_vec(p)})
        elif tag == 'uv_list' and m.uvs is not None:
            lst = ET.SubElement(v, tag)
            for i, p in enumerate(m.uvs):
                val = fmt_vec(p) if m.uv_width == 2 else fmt_vec(p[:1]) + ' '
                ET.SubElement(lst, 'uv', {'n': str(i), 'value': val})
    ia = {'count': str(len(m.faces))}
    ia.update(m.index_attrs or {'b32bit': '1' if len(m.positions) > 65535 else '0'})
    idx = ET.SubElement(e, 'index', ia)
    for i, (a, b, c) in enumerate(m.faces):
        ET.SubElement(idx, 'face', {'n': str(i), 'a': str(a), 'b': str(b), 'c': str(c)})
    return e


def used_bone_count(m: Mesh) -> int:
    if not m.skin:
        return 0
    return len({b for infl in m.skin for b, w in infl if w > 0})


DEFAULT_SECTION_ORDER = ['header', 'material_list', 'mesh_list', 'skinmesh_list',
                         'special_bone_list', 'bound_box']


def model_to_xml(model: Model) -> bytes:
    statics = [m for m in model.meshes if m.kind == 'mesh']
    skins = [m for m in model.meshes if m.kind == 'skinmesh']
    header = copy.deepcopy(model.header) if model.header is not None else _default_header()
    for key, n in (('material', len(model.materials)), ('mesh', len(statics)), ('skinmesh', len(skins))):
        # the engine ignores these counts; some shipped headers disagree with their lists, so
        # an untouched list keeps the original (inconsistent) value
        if model.list_counts.get(key) != n:
            header.set(key, str(n))
    order = [t for t, _ in model.sections] or list(DEFAULT_SECTION_ORDER)
    for tag in ('header', 'material_list'):
        if tag not in order:
            order.insert(0 if tag == 'header' else 1, tag)
    # keep mesh lists that exist now, drop ones that became empty
    for tag, lst in (('mesh_list', statics), ('skinmesh_list', skins)):
        if lst and tag not in order:
            pos = order.index('material_list') + 1 + (1 if tag == 'skinmesh_list' and 'mesh_list' in order else 0)
            order.insert(pos, tag)
    if 'bound_box' not in order:
        order.append('bound_box')
    raw = dict((t, e) for t, e in model.sections if e is not None)
    out = []
    for tag in order:
        if tag == 'header':
            out.append(header)
        elif tag == 'material_list':
            e = ET.Element('material_list', {'count': _list_count(model, 'material', len(model.materials))})
            for m in model.materials:
                e.append(m.element)
            orig = model.raw.get('material_list')
            if orig and _same_elements(orig, e):
                out.append(orig)                  # unchanged: keep original whitespace/tabs
            else:
                out.append(e)
        elif tag in ('mesh_list', 'skinmesh_list'):
            lst = statics if tag == 'mesh_list' else skins
            e = ET.Element(tag, {'count': _list_count(model, tag[:-5], len(lst))})
            for m in lst:
                e.append(_mesh_element(m, model.exp3))
            out.append(e)
        elif tag == 'bound_box':
            boxes = [transform_bbox(m.bound_box or mesh_bbox(m), m.transform) if m.kind == 'mesh'
                     else (m.bound_box or mesh_bbox(m)) for m in model.meshes]
            if boxes:
                bb = [min(b[i] for b in boxes) for i in range(3)] + [max(b[i] for b in boxes) for i in range(3, 6)]
                orig = model.raw.get('bound_box')
                if orig:          # not read by the engine's model loader; kept while meshes are unchanged
                    out.append(orig)
                else:
                    out.append(ET.Element('bound_box', _bbox_attrs(bb, model.exp3)))
        elif tag in model.raw and tag in raw:
            out.append(model.raw[tag])
        elif tag in raw:
            out.append(raw[tag])
    return serialize(out, indent=model.indent)


def _default_header() -> ET.Element:
    h = ET.Element('header', {k: '0' for k in ('material', 'mesh', 'skinmesh', 'bone', 'special_bone',
                                                'camera', 'light', 'helper', 'physics')})
    ET.SubElement(h, 'frame', {'ticksperframe': '160', 'framerate': '30', 'start': '0', 'end': '100'})
    return h


# --------------------------------------------------------------------------- skeleton

@dataclass
class Bone:
    id: int
    name: str
    parent_id: int
    parent_name: str
    world: list                                  # bind-pose world <transform>
    init_pos: tuple | None = None
    init_rot: tuple | None = None
    init_scale: tuple | None = None
    worldid: int | None = None                   # special bones only


@dataclass
class Skeleton:
    path: str | None
    header: ET.Element | None
    bones: list[Bone]
    helpers: ET.Element | None

    def by_name(self) -> dict[str, Bone]:
        return {b.name: b for b in self.bones}


def _read_bone(b: ET.Element) -> Bone:
    p = b.find('parent')
    bone = Bone(ival(b, 'id'), b.get('name', ''), ival(p, 'id', -1) if p is not None else -1,
                p.get('name', '') if p is not None else '',
                read_tm(b.find('transform')) or copy.deepcopy(IDENTITY))
    ik = b.find('init_key')
    if ik is not None:
        if ik.get('pos'):
            bone.init_pos = tuple(floats(ik.get('pos'))[:3])
        if ik.get('rot'):
            bone.init_rot = tuple(floats(ik.get('rot'))[:4])
        if ik.get('scale'):
            bone.init_scale = tuple(floats(ik.get('scale'))[:3])
    if b.get('worldid') is not None:
        bone.worldid = ival(b, 'worldid')
    return bone


def load_skeleton(path: str) -> Skeleton:
    root = parse_file(path)
    bl = root.find('bonelist')
    bones = [_read_bone(b) for b in bl] if bl is not None else []
    bones.sort(key=lambda b: b.id)
    return Skeleton(path, root.find('header'), bones, root.find('helper_list'))


# --------------------------------------------------------------------------- animation

@dataclass
class Track:
    id: int
    name: str | None
    parent_name: str | None
    pos: list          # [(n, (x, y, z))]
    rot: list          # [(n, (x, y, z, w))]
    scale: list        # [(n, (x, y, z))]


@dataclass
class Animation:
    path: str | None
    framerate: int
    start: int
    end: int
    ticksperframe: int | None
    bones: list[Track]
    meshes: list[Track]
    extra: list[tuple[str, ET.Element]]       # tag_list, sound_list, effect_list, material anim
    flavour: str                              # 'multi-root' | 'root'
    header: ET.Element | None = None


def _read_keys(k: ET.Element | None, width: int):
    if k is None:
        return []
    out = []
    for pos, x in enumerate(k):
        n = x.get('n')
        vals = floats(x.get('value'))
        if len(vals) < width:
            vals += [0.0] * (width - len(vals))
        out.append((int(n) if n is not None else pos, tuple(vals[:width])))
    return out


def _read_tracks(lst: ET.Element | None) -> list[Track]:
    if lst is None:
        return []
    out = []
    for a in lst:
        p = a.find('parent')
        out.append(Track(ival(a, 'id'), a.get('name'), p.get('name') if p is not None else None,
                         _read_keys(a.find('pos_key'), 3), _read_keys(a.find('rot_key'), 4),
                         _read_keys(a.find('scale_key'), 3)))
    return out


def load_animation(path: str) -> Animation:
    root = parse_file(path)
    h = root.find('header')
    fr = h.find('frame') if h is not None else None
    extra = [(c.tag, c) for c in root
             if c.tag not in ('header', 'mesh_animation_list', 'bone_animation_list')]
    return Animation(path, ival(fr, 'framerate', 30) if fr is not None else 30,
                     ival(fr, 'start') if fr is not None else 0,
                     ival(fr, 'end') if fr is not None else 0,
                     ival(fr, 'ticksperframe') if fr is not None and fr.get('ticksperframe') else None,
                     _read_tracks(root.find('bone_animation_list')),
                     _read_tracks(root.find('mesh_animation_list')),
                     extra, root.get('wrapped', 'multi-root'), h)


def animation_to_xml(anim: Animation) -> bytes:
    """Write a .nwat in the compact ``<root>`` flavour (what most shipped character anims use)."""
    h = ET.Element('header', {'mesh': str(len(anim.meshes)), 'bone': str(len(anim.bones))})
    ET.SubElement(h, 'frame', {'framerate': str(anim.framerate), 'start': str(anim.start),
                               'end': str(anim.end)})
    out = [h]
    for tag, tracks in (('mesh_animation_list', anim.meshes), ('bone_animation_list', anim.bones)):
        lst = ET.Element(tag, {'count': str(len(tracks))})
        for t in tracks:
            a = ET.SubElement(lst, 'animation', {'id': str(t.id)})
            for ktag, ktag1, keys in (('pos_key', 'pos', t.pos), ('rot_key', 'rot', t.rot),
                                      ('scale_key', 'scale', t.scale)):
                k = ET.SubElement(a, ktag, {'count': str(len(keys))})
                for n, val in keys:
                    ET.SubElement(k, ktag1, {'n': str(n), 'value': fmt_vec(val)})
        out.append(lst)
    for tag, e in anim.extra:
        out.append(e)
    return serialize(out, declaration='<?xml version="1.0" encoding="euc-kr" ?>', wrap_root=True)


def skeleton_bone_count(path: str) -> int:
    with open(path, 'rb') as f:
        head = f.read(512)
    m = re.search(rb'<header[^>]*\sbone="(\d+)"', head)
    return int(m.group(1)) if m else -1


def guess_skeleton(model_path: str, bone_count: int | None = None) -> str | None:
    """Find the .nwbt a skinned model was made for.

    Candidates, in order: same-folder .nwbt with the same stem, the shared character skeleton
    for ``f1_``/``m1_`` items (``character/Character/F1/f1.nwbt``), other .nwbt files in the
    same folder.  When ``bone_count`` is given (the model header's ``bone``), only skeletons
    with exactly that many bones are accepted.
    """
    d = os.path.dirname(os.path.abspath(model_path))
    stem = os.path.splitext(os.path.basename(model_path))[0].lower()
    local = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith('.nwbt'))
    cands = [f for f in local if os.path.splitext(os.path.basename(f))[0].lower() == stem]
    prefix = stem.split('_')[0]
    if prefix in ('f1', 'm1'):
        cur = d
        for _ in range(8):
            for sub in (('character', 'Character'), ('Character',)):
                c = os.path.join(cur, *sub, prefix.upper(), prefix + '.nwbt')
                if os.path.exists(c):
                    cands.append(c)
            nxt = os.path.dirname(cur)
            if nxt == cur:
                break
            cur = nxt
    cands += local
    seen = set()
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        if bone_count is None or skeleton_bone_count(c) == bone_count:
            return c
    return None
