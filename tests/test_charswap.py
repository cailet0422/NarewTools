"""Character swap: model readers, retargeting, the studio service and the agent tools.

    python tests/test_charswap.py <game dir> <scratch dir> [model file]

Copies the character/item/npc/xml packs into <scratch>\\game (read-only use), points APPDATA/LOCALAPPDATA/
USERPROFILE at the scratch folder and checks:
* FBX (binary 7.4 and ASCII), OBJ+MTL and PMX files written here are read with the right axes, units,
  skin weights, UVs and textures;
* swapping the game's own female character (exported to glTF) onto its own skeleton gives back the
  original joints and animation keys (identity retarget), and onto the male skeleton keeps the
  animation's bone directions exactly;
* hiding a part keeps its materials and helpers; the service writes/undoes the swap in a project;
* the agent tools (inspect, dry run with a picture, swap, status, remove).
An optional [model file] (e.g. an FBX character) is inspected and dry-run swapped as well.
"""
import io
import json
import os
import shutil
import struct
import sys
import zlib

GAME_SRC, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
EXTRA = sys.argv[3] if len(sys.argv) > 3 else None
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(SCRATCH)
HOME = os.path.join(SCRATCH, 'home')
os.makedirs(os.path.join(HOME, 'Documents'))
os.environ.update({'APPDATA': os.path.join(SCRATCH, 'appdata'), 'LOCALAPPDATA': os.path.join(SCRATCH, 'localappdata'),
                   'USERPROFILE': HOME})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GAME = os.path.join(SCRATCH, 'game')
os.makedirs(os.path.join(GAME, 'data'))
for stem in ('xml', 'character', 'item', 'npc'):
    shutil.copy2(os.path.join(GAME_SRC, 'data', stem + '.pack'), os.path.join(GAME, 'data', stem + '.pack'))
open(os.path.join(GAME, 'IsletClient_release(x64).exe'), 'wb').close()

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from narew import charswap, fbx, scene3d  # noqa: E402
from narew.gltf import trs_to_rowmat  # noqa: E402
from narew.nwmodel import load_animation, load_model, load_skeleton  # noqa: E402

checks = []


def check(name, ok, info=''):
    ok = bool(ok)
    checks.append((name, ok))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or info is None or (isinstance(info, str) and not info)
                                                 else f'  [{info}]'), flush=True)


def close(a, b, eps=1e-4):
    return np.allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), atol=eps)


def png_bytes(color=(200, 50, 50)) -> bytes:
    out = io.BytesIO()
    Image.new('RGBA', (8, 8), color + (255,)).save(out, 'PNG')
    return out.getvalue()


WORK = os.path.join(SCRATCH, 'models')
os.makedirs(WORK)

# =========================================================================== FBX writer (test data)
# a 2-bone chain in a 3ds Max style file (Z up, -Y front, centimetres) with a skinned quad and an
# embedded texture.  Node = (name, [props], [children]); prop = python value or ('d'|'i', list)


def fbx_tree(tex: bytes, ascii_mode: bool):
    def P(name, t1, t2, flag, *vals):
        return ('P', [name, t1, t2, flag, *vals], [])

    def p70(*ps):
        return ('Properties70', [], list(ps))

    def obj(kind, oid, name, cls, children):
        return (kind, [oid, (f'{kind}::{name}' if ascii_mode else f'{name}\x00\x01{kind}'), cls], children)

    def mat16(t):
        m = np.eye(4)
        m[:3, 3] = t
        return ('d', list(m.T.reshape(-1)))          # column-major for column vectors

    verts = [-10, 0, 100, 10, 0, 100, 10, 0, 150, -10, 0, 150]
    geom = obj('Geometry', 10, 'Quad', 'Mesh', [
        ('Vertices', [('d', verts)], []),
        ('PolygonVertexIndex', [('i', [0, 1, 2, -4])], []),
        ('LayerElementNormal', [0], [('MappingInformationType', ['ByPolygonVertex'], []),
                                     ('ReferenceInformationType', ['Direct'], []),
                                     ('Normals', [('d', [0, -1, 0] * 4)], [])]),
        ('LayerElementUV', [0], [('MappingInformationType', ['ByPolygonVertex'], []),
                                 ('ReferenceInformationType', ['IndexToDirect'], []),
                                 ('UV', [('d', [0, 0, 1, 0, 1, 1, 0, 1])], []),
                                 ('UVIndex', [('i', [0, 1, 2, 3])], [])]),
        ('LayerElementMaterial', [0], [('MappingInformationType', ['AllSame'], []),
                                       ('ReferenceInformationType', ['IndexToDirect'], []),
                                       ('Materials', [('i', [0])], [])]),
    ])
    objects = ('Objects', [], [
        obj('Model', 1, 'RootBone', 'LimbNode', [p70(P('Lcl Translation', 'Lcl Translation', '', 'A', 0.0, 0.0, 100.0))]),
        obj('Model', 2, 'ChildBone', 'LimbNode', [p70(P('Lcl Translation', 'Lcl Translation', '', 'A', 0.0, 0.0, 50.0))]),
        geom,
        obj('Model', 11, 'Quad', 'Mesh', [p70()]),
        obj('Material', 20, 'Skin', '', [p70(P('DiffuseColor', 'Color', '', 'A', 1.0, 1.0, 1.0))]),
        obj('Texture', 21, 'tex', '', [('FileName', ['C:\\nowhere\\tex.png'], []), ('RelativeFilename', ['tex.png'], [])]),
        obj('Video', 22, 'tex', 'Clip', [('Content', [tex if not ascii_mode else ''], []),
                                          ('RelativeFilename', ['tex.png'], [])]),
        obj('Deformer', 30, 'Skin', 'Skin', []),
        obj('Deformer', 31, 'C1', 'Cluster', [('Indexes', [('i', [0, 1])], []), ('Weights', [('d', [1.0, 1.0])], []),
                                              ('Transform', [mat16((0, 0, -100))], []),
                                              ('TransformLink', [mat16((0, 0, 100))], [])]),
        obj('Deformer', 32, 'C2', 'Cluster', [('Indexes', [('i', [2, 3])], []), ('Weights', [('d', [1.0, 1.0])], []),
                                              ('Transform', [mat16((0, 0, -150))], []),
                                              ('TransformLink', [mat16((0, 0, 150))], [])]),
    ])
    conns = ('Connections', [], [('C', ['OO', a, b], []) for a, b in
                                 ((1, 0), (2, 1), (11, 0), (10, 11), (20, 11), (22, 21), (30, 10), (31, 30), (32, 30),
                                  (1, 31), (2, 32))] + [('C', ['OP', 21, 20, 'DiffuseColor'], [])])
    gs = ('GlobalSettings', [], [('Version', [1000], []), p70(
        P('UpAxis', 'int', 'Integer', '', 2), P('UpAxisSign', 'int', 'Integer', '', 1),
        P('FrontAxis', 'int', 'Integer', '', 1), P('FrontAxisSign', 'int', 'Integer', '', -1),
        P('CoordAxis', 'int', 'Integer', '', 0), P('CoordAxisSign', 'int', 'Integer', '', 1),
        P('UnitScaleFactor', 'double', 'Number', '', 1.0))])
    head = ('FBXHeaderExtension', [], [('FBXHeaderVersion', [1003], []), ('FBXVersion', [7400], [])])
    return [head, gs, objects, conns]


def fbx_binary(nodes) -> bytes:
    def prop(v):
        if isinstance(v, tuple):
            code, vals = v
            raw = struct.pack(f'<{len(vals)}{"d" if code == "d" else "i"}', *vals)
            comp = zlib.compress(raw)
            return code.encode() + struct.pack('<III', len(vals), 1, len(comp)) + comp
        if isinstance(v, bool):
            return b'C' + bytes([v])
        if isinstance(v, int):
            return b'L' + struct.pack('<q', v) if abs(v) >= 2 ** 31 else b'I' + struct.pack('<i', v)
        if isinstance(v, float):
            return b'D' + struct.pack('<d', v)
        if isinstance(v, bytes):
            return b'R' + struct.pack('<I', len(v)) + v
        s = str(v).encode('utf-8')
        return b'S' + struct.pack('<I', len(s)) + s

    def node(n, offset):
        name, props, children = n
        pb = b''.join(prop(p) for p in props)
        head_len = 13 + len(name)
        body = b''
        pos = offset + head_len + len(pb)
        for c in children:
            cb = node(c, pos)
            body += cb
            pos += len(cb)
        if children:
            body += b'\0' * 13
        end = offset + head_len + len(pb) + len(body)
        return struct.pack('<III', end, len(props), len(pb)) + bytes([len(name)]) + name.encode() + pb + body

    out = b'Kaydara FBX Binary  \x00\x1a\x00' + struct.pack('<I', 7400)
    for n in nodes:
        out += node(n, len(out))
    return out + b'\0' * 13


def fbx_ascii(nodes) -> str:
    def val(v):
        if isinstance(v, tuple):
            return None
        if isinstance(v, float):
            return repr(v)
        if isinstance(v, int):
            return str(v)
        return '"' + str(v) + '"'

    lines = ['; FBX 7.4.0 project file']

    def node(n, ind):
        name, props, children = n
        arr = [p for p in props if isinstance(p, tuple)]
        if arr:
            vals = arr[0][1]
            lines.append(f'{ind}{name}: *{len(vals)} {{')
            lines.append(f'{ind}\ta: ' + ','.join(repr(float(x)) if arr[0][0] == 'd' else str(int(x)) for x in vals))
            lines.append(f'{ind}}}')
            return
        head = f'{ind}{name}: ' + ', '.join(val(p) for p in props)
        if children:
            lines.append(head + ' {')
            for c in children:
                node(c, ind + '\t')
            lines.append(f'{ind}}}')
        else:
            lines.append(head)

    for n in nodes:
        node(n, '')
    return '\n'.join(lines) + '\n'


tex = png_bytes()
p_bin = os.path.join(WORK, 'bin', 'chain.fbx')
p_asc = os.path.join(WORK, 'asc', 'chain.fbx')
os.makedirs(os.path.dirname(p_bin))
os.makedirs(os.path.dirname(p_asc))
with open(p_bin, 'wb') as f:
    f.write(fbx_binary(fbx_tree(tex, False)))
with open(p_asc, 'w', encoding='utf-8') as f:
    f.write(fbx_ascii(fbx_tree(tex, True)))
with open(os.path.join(WORK, 'asc', 'tex.png'), 'wb') as f:
    f.write(png_bytes((10, 200, 10)))

for label, p in (('binary', p_bin), ('ascii', p_asc)):
    try:
        sc = fbx.load_scene(p)
    except Exception as ex:
        check(f'fbx {label} loads', False, repr(ex))
        continue
    check(f'fbx {label} loads', True)
    names = [b.name for b in sc.bones]
    check(f'fbx {label} bones', names == ['RootBone', 'ChildBone'], names)
    check(f'fbx {label} Z-up cm -> Y-up m', close(sc.bones[0].head, (0, 1.0, 0)) and close(sc.bones[1].head, (0, 1.5, 0)),
          [b.head for b in sc.bones])
    m = sc.meshes[0]
    check(f'fbx {label} quad -> 2 triangles', len(m.faces) == 2 and len(m.positions) == 4, (len(m.faces), len(m.positions)))
    ys = sorted(np.round(m.positions[:, 1], 4).tolist())
    check(f'fbx {label} vertex heights', close(ys, [1.0, 1.0, 1.5, 1.5]), ys)
    lo = m.positions[:, 1] < 1.25
    check(f'fbx {label} skin weights', (m.joints[lo, 0] == 0).all() and (m.joints[~lo, 0] == 1).all()
          and close(m.weights[:, 0], 1.0), (m.joints.tolist(), m.weights.tolist()))
    check(f'fbx {label} uv flipped to top-left', close(sorted(m.uvs[:, 1]), [0, 0, 1, 1]) and
          close(m.uvs[np.argmax(m.positions[:, 1] + m.positions[:, 0])], (1, 0)), m.uvs.tolist())
    n = np.cross(m.positions[m.faces[0, 1]] - m.positions[m.faces[0, 0]],
                 m.positions[m.faces[0, 2]] - m.positions[m.faces[0, 0]])
    check(f'fbx {label} winding matches normals', float(np.dot(n, m.normals[m.faces[0, 0]])) > 0, (n, m.normals[0]))
    img = sc.materials[0].image if sc.materials else None
    want = (200, 50, 50) if label == 'binary' else (10, 200, 10)
    got = Image.open(io.BytesIO(img)).convert('RGB').getpixel((0, 0)) if img else None
    check(f'fbx {label} texture ({"embedded" if label == "binary" else "next to the file"})', got == want, got)

# =========================================================================== OBJ
d_obj = os.path.join(WORK, 'obj')
os.makedirs(d_obj)
with open(os.path.join(d_obj, 'box.obj'), 'w') as f:
    f.write('mtllib box.mtl\nv 0 0 0\nv 1 0 0\nv 1 2 0\nv 0 2 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n'
            'usemtl red\nf 1/1 2/2 3/3 4/4\n')
with open(os.path.join(d_obj, 'box.mtl'), 'w') as f:
    f.write('newmtl red\nKd 1 0 0\nmap_Kd -s 1 1 1 red.png\n')
with open(os.path.join(d_obj, 'red.png'), 'wb') as f:
    f.write(png_bytes((255, 0, 0)))
sc = scene3d.load(os.path.join(d_obj, 'box.obj'))
check('obj loads', len(sc.meshes) == 1 and len(sc.meshes[0].faces) == 2 and not sc.bones)
check('obj texture from mtl (with options)', sc.materials and sc.materials[0].image is not None,
      [m.image_name for m in sc.materials])
check('obj uv flipped', close(sorted(sc.meshes[0].uvs[:, 1]), [0, 0, 1, 1]))

# =========================================================================== PMX


def pmx_bytes():
    def text(s):
        b = s.encode('utf-16-le')
        return struct.pack('<i', len(b)) + b
    out = b'PMX ' + struct.pack('<f', 2.0) + bytes([8, 0, 0, 2, 1, 1, 2, 1, 1])
    out += text('t') + text('t') + text('') + text('')
    verts = [((0, 10, 0), 0), ((1, 10, 0), 0), ((1, 15, 0), 1), ((0, 15, 0), 1)]
    out += struct.pack('<i', len(verts))
    for (x, y, z), b in verts:
        out += struct.pack('<8f', x, y, z, 0, 0, -1, 0, 0) + bytes([0]) + struct.pack('<h', b) + struct.pack('<f', 1.0)
    idx = [0, 1, 2, 0, 2, 3]
    out += struct.pack('<i', len(idx)) + struct.pack(f'<{len(idx)}H', *idx)
    out += struct.pack('<i', 1) + text('tex.png')
    out += struct.pack('<i', 1) + text('mat') + text('mat') + struct.pack('<4f', 1, 1, 1, 1) + struct.pack('<3f', 0, 0, 0)
    out += struct.pack('<f', 1) + struct.pack('<3f', 0, 0, 0) + bytes([1]) + struct.pack('<4f', 0, 0, 0, 1)
    out += struct.pack('<f', 1) + bytes([0]) + bytes([0xff]) + bytes([0]) + bytes([1, 0]) + text('')
    out += struct.pack('<i', len(idx))
    out += struct.pack('<i', 2)
    for name, pos, parent in (('センター', (0, 8, 0), -1), ('上半身', (0, 12, 0), 0)):
        out += text(name) + text('') + struct.pack('<3f', *pos) + struct.pack('<h', parent) + struct.pack('<i', 0)
        out += struct.pack('<H', 0x0002) + struct.pack('<3f', 0, 1, 0)
    return out


p_pmx = os.path.join(WORK, 'pmx', 'mini.pmx')
os.makedirs(os.path.dirname(p_pmx))
with open(p_pmx, 'wb') as f:
    f.write(pmx_bytes())
with open(os.path.join(WORK, 'pmx', 'tex.png'), 'wb') as f:
    f.write(png_bytes((0, 0, 255)))
try:
    sc = scene3d.load(p_pmx)
    check('pmx loads', len(sc.meshes[0].faces) == 2 and [b.name for b in sc.bones] == ['センター', '上半身'])
    check('pmx bone heads', close(sc.bones[1].head, (0, 12, 0)))
    check('pmx weights', sc.meshes[0].joints[2, 0] == 1 and close(sc.meshes[0].weights[:, 0], 1.0))
    check('pmx texture', sc.materials[0].image is not None and sc.materials[0].double_sided)
    check('pmx mirrored into right-handed space', close(sc.meshes[0].normals[0], (0, 0, 1)), sc.meshes[0].normals[0])
except Exception as ex:
    check('pmx loads', False, repr(ex))

# =========================================================================== bone names
cases = {'mixamorig:LeftUpLeg': ('L', 'thigh'), 'mixamorig:LeftLeg': ('L', 'leg'), 'RightHandIndex1': ('R', 'index'),
         'J_Bip_L_UpperArm': ('L', 'upperarm'), 'J_Bip_R_LowerArm': ('R', 'forearm'), 'upper_arm.L': ('L', 'upperarm'),
         'DEF-forearm.R': ('R', 'forearm'), 'Bip001 L Finger0': ('L', 'thumb'), 'Bip001 R ForeTwist': ('R', 'twist'),
         'calf_l': ('L', 'calf'), 'thigh_r': ('R', 'thigh'), 'Bip001 Head': (None, 'head'), 'Bip001 HeadNub': (None, 'nub'),
         '左腕': ('L', 'upperarm'), '右ひじ': ('R', 'forearm'), '左足首': ('L', 'foot'), '下半身': (None, 'hips'),
         'Neck': (None, 'neck'), 'Hips': (None, 'hips'), 'LeftToeBase': ('L', 'toe'), 'CC_Base_L_Clavicle': ('L', 'clavicle')}
bad = {n: (charswap.classify(n), w) for n, w in cases.items() if charswap.classify(n) != w}
check('bone name rules', not bad, bad)

# =========================================================================== studio + identity retarget
from studio.core import Studio  # noqa: E402
from studio.svc_charswap import GENDERS, CharSwapService  # noqa: E402
from studio.svc_models import ModelService  # noqa: E402
from narew.modfmt import ModInfo  # noqa: E402

st = Studio()
st.open_game(GAME)
st.new_project(os.path.join(SCRATCH, 'projects'), ModInfo(id='swap-test', name='교체 테스트'))
ms = ModelService(st)
svc = CharSwapService(st)
parts = ['Item\\Default\\f1_body01.nwmt', 'Item\\Default\\f1_face01.nwmt', 'Item\\Default\\f1_hair01.nwmt']
src_glb = ms.glb(parts, [], 'Character\\F1\\f1.nwbt')
check('islet character exported to glTF', os.path.isfile(src_glb))
ins = svc.inspect(src_glb)
check('islet character: humanoid found', ins['humanoid'] and ins['mapping'].get('hand.L') == 'Bip001 L Hand', ins.get('mapping'))
check('islet character: spine', ins.get('spine') == ['Bip001 Spine', 'Bip001 Spine1'], ins.get('spine'))



def glb_edit(src, dst, fn):
    """Rewrite a .glb's JSON chunk with fn(json)."""
    data = open(src, 'rb').read()
    off, chunks = 12, []
    while off < len(data):
        ln, typ = struct.unpack_from('<II', data, off)
        chunks.append([typ, data[off + 8:off + 8 + ln]])
        off += 8 + ln
    js = json.loads(chunks[0][1].decode('utf-8'))
    fn(js)
    raw = json.dumps(js).encode('utf-8')
    raw += b' ' * (-len(raw) % 4)
    chunks[0][1] = raw
    body = b''.join(struct.pack('<II', len(c), t) + c for t, c in chunks)
    open(dst, 'wb').write(b'glTF' + struct.pack('<II', 2, 12 + len(body)) + body)


UE = {'Bip001': 'root', 'Bip001 Pelvis': 'pelvis', 'Bip001 Spine': 'spine_01', 'Bip001 Spine1': 'spine_02',
      'Bip001 Neck': 'neck_01', 'Bip001 Head': 'head', 'Bip001 Footsteps': 'ik_foot_root', 'Bip001 Xtra01': 'skirt_f',
      'Bip001 Xtra02': 'skirt_b'}
for s, l in (('L', 'l'), ('R', 'r')):
    UE.update({f'Bip001 {s} Clavicle': f'clavicle_{l}', f'Bip001 {s} UpperArm': f'upperarm_{l}',
               f'Bip001 {s} Forearm': f'lowerarm_{l}', f'Bip001 {s} Hand': f'hand_{l}', f'Bip001 {s} Finger0': f'thumb_01_{l}',
               f'Bip001 {s} Finger1': f'index_01_{l}', f'Bip001 {s} ForeTwist': f'lowerarm_twist_01_{l}',
               f'Bip001 {s} Thigh': f'thigh_{l}', f'Bip001 {s} Calf': f'calf_{l}', f'Bip001 {s} Foot': f'foot_{l}',
               f'Bip001 {s} Toe0': f'ball_{l}'})


def to_ue(js):
    for n in js['nodes']:
        n['name'] = UE.get(n.get('name'), n.get('name'))


ue_glb = os.path.join(WORK, 'ue.glb')
glb_edit(src_glb, ue_glb, to_ue)
sc = scene3d.load(ue_glb)
rig = charswap.detect_rig(sc)
want = {'hips': 'pelvis', 'hand.L': 'hand_l', 'forearm.R': 'lowerarm_r', 'upperarm.L': 'upperarm_l', 'thigh.R': 'thigh_r',
        'calf.L': 'calf_l', 'foot.R': 'foot_r', 'toe.L': 'ball_l', 'head': 'head', 'neck': 'neck_01', 'clavicle.L': 'clavicle_l'}
got = {k: sc.bones[rig.slots[k]].name for k in want if rig and rig.slots.get(k) is not None}
check('UE-style names on a real skeleton', got == want, got)
check('UE-style spine + twist', rig and [sc.bones[b].name for b in rig.spine] == ['spine_01', 'spine_02'] and
      sorted(set(rig.twist.values())) == ['foretwist.L', 'foretwist.R'], rig and (rig.spine, rig.twist))

VRM_KEYS = {'hips': 'Bip001 Pelvis', 'neck': 'Bip001 Neck', 'head': 'Bip001 Head'}
for s, w in (('L', 'left'), ('R', 'right')):
    VRM_KEYS.update({f'{w}UpperArm': f'Bip001 {s} UpperArm', f'{w}LowerArm': f'Bip001 {s} Forearm',
                     f'{w}Hand': f'Bip001 {s} Hand', f'{w}UpperLeg': f'Bip001 {s} Thigh',
                     f'{w}LowerLeg': f'Bip001 {s} Calf', f'{w}Foot': f'Bip001 {s} Foot', f'{w}Toes': f'Bip001 {s} Toe0'})


def to_vrm(js):
    index = {n.get('name'): i for i, n in enumerate(js['nodes'])}
    js.setdefault('extensions', {})['VRMC_vrm'] = {'specVersion': '1.0', 'humanoid': {
        'humanBones': {k: {'node': index[v]} for k, v in VRM_KEYS.items()}}}
    for i, n in enumerate(js['nodes']):
        n['name'] = f'node{i:03d}'                     # names say nothing: the humanoid map must do it


vrm = os.path.join(WORK, 'model.vrm')
glb_edit(src_glb, vrm, to_vrm)
sc = scene3d.load(vrm)
rig = charswap.detect_rig(sc)
check('VRM: humanoid map read', sc.fmt == 'VRM' and len(sc.humanoid) == len(VRM_KEYS), (sc.fmt, len(sc.humanoid)))
ok_vrm = rig is not None and all(rig.slots.get(k) == sc.humanoid[k] for k in sc.humanoid)
check('VRM: rig from the humanoid map (unnamed bones)', ok_vrm, rig and rig.notes)

old_skel = st.read('Character\\F1\\f1.nwbt', 'game')
H = svc.ref_height('f1')
check('reference height ~1.9', 1.6 < H < 2.2, H)
sc, rig, rep = charswap.prepare_source(src_glb, H)
tgt = charswap.Target(old_skel)
charswap.repose(sc, rig, tgt, arm_angle=0.0, leg_angle=0.0)
ch = charswap.build(sc, rig, tgt, st.read(GENDERS['f1']['body'], 'game'), 'cs_test', 'f1')
newpos = np.array([m[:3, 3] for m in ch.bones_new])
oldpos = np.array([b.world[:3, 3] for b in tgt.bones])
err = np.abs(newpos - oldpos).max(axis=1)
mapped = [i for i, b in enumerate(tgt.bones) if b.slot in ('hips', 'head', 'neck', 'upperarm.L', 'forearm.R', 'hand.L',
                                                           'thigh.L', 'calf.R', 'foot.L', 'spine0', 'spine1')]
check('identity: joints back where they were', err[mapped].max() < 0.02, dict(zip([tgt.bones[i].name for i in mapped],
                                                                                 np.round(err[mapped], 4))))
check('identity: hip scale ~1', abs(ch.report['hip_scale'] - 1) < 0.03, ch.report['hip_scale'])
a_name = 'Character\\F1\\f1_common_run.nwat'
a_old = st.read(a_name, 'game')
a_new = charswap.retarget_animation(a_old, ch.plan)
pa = os.path.join(SCRATCH, 'a_old.nwat')
pb = os.path.join(SCRATCH, 'a_new.nwat')
open(pa, 'wb').write(a_old)
open(pb, 'wb').write(a_new)
A, B = load_animation(pa), load_animation(pb)
same_rot = all(ta.rot == tb.rot and ta.scale == tb.scale for ta, tb in zip(A.bones, B.bones))
check('retarget keeps rotations byte for byte', same_rot)
dmax = max(max(np.abs(np.array(va) - np.array(vb)).max() for (_, va), (_, vb) in zip(ta.pos, tb.pos))
           for ta, tb in zip(A.bones, B.bones) if ta.pos)
check('identity: position keys ~unchanged', dmax < 0.02, dmax)
check('retarget keeps the rest of the file', a_new.count(b'<rot ') == a_old.count(b'<rot ') and
      a_new[-200:] == a_old[-200:])


def pose(skel_path, anim_path, frame):
    sk, an = load_skeleton(skel_path), load_animation(anim_path)
    tr = {t.id: t for t in an.bones}
    W = {}
    for b in sk.bones:
        t = tr.get(b.id)

        def key(lst, d):
            return lst[min(frame, len(lst) - 1)][1] if lst else d
        L = np.array(trs_to_rowmat(key(t.pos, b.init_pos) if t else b.init_pos, key(t.rot, b.init_rot) if t else b.init_rot,
                                   key(t.scale, b.init_scale) if t else b.init_scale))
        W[b.id] = L if b.parent_id < 0 else L @ W[b.parent_id]
    return {i: m[3, :3] for i, m in W.items()}


# cross: the same model onto the male skeleton, real proportions - bone directions must match the original anim
sc2, rig2, _ = charswap.prepare_source(src_glb, 1.7)
tgt_m = charswap.Target(st.read('Character\\M1\\m1.nwbt', 'game'))
charswap.repose(sc2, rig2, tgt_m, arm_angle=0.0, leg_angle=0.0)
chm = charswap.build(sc2, rig2, tgt_m, st.read(GENDERS['m1']['body'], 'game'), 'cs_test', 'm1')
ps_old, ps_new = os.path.join(SCRATCH, 'm1_old.nwbt'), os.path.join(SCRATCH, 'm1_new.nwbt')
open(ps_old, 'wb').write(st.read('Character\\M1\\m1.nwbt', 'game'))
open(ps_new, 'wb').write(chm.skeleton)
an_old, an_new = os.path.join(SCRATCH, 'm1a_old.nwat'), os.path.join(SCRATCH, 'm1a_new.nwat')
m1run = st.read('Character\\M1\\common_run.nwat', 'game')
open(an_old, 'wb').write(m1run)
open(an_new, 'wb').write(charswap.retarget_animation(m1run, chm.plan))
worst = 0.0
names = {b.slot: b.id for b in tgt_m.bones}
for f in (0, 5, 11):
    po, pn = pose(ps_old, an_old, f), pose(ps_new, an_new, f)
    for a, b in (('upperarm.L', 'forearm.L'), ('forearm.R', 'hand.R'), ('thigh.L', 'calf.L'), ('calf.R', 'foot.R'),
                 ('spine0', 'spine1')):
        da = po[names[b]] - po[names[a]]
        db = pn[names[b]] - pn[names[a]]
        worst = max(worst, float(np.linalg.norm(da / np.linalg.norm(da) - db / np.linalg.norm(db))))
check('male retarget: limb directions follow the animation', worst < 0.02, worst)
mb = os.path.join(SCRATCH, 'm1_body.nwmt')
open(mb, 'wb').write(chm.body)
mdl = load_model(mb)
ok_skin = all(all(0 <= b < 29 for b, w in v) and abs(sum(w for _, w in v[:3]) + v[3][1] - 1) < 1e-4 and
              v[3][1] >= 0 for m in mdl.meshes for v in m.skin)
check('body skin: 4 influences, weights sum to 1, bones < 29', ok_skin)
check('attach points kept (F1 body helpers, M1 skeleton helpers)', b'Point_Hand_R' in ch.body and
      b'Point_Hand_R' in chm.skeleton and b'Point_Spine_B' in chm.skeleton)
check('body material: own texture + black mask', all(m.map('mask_map') == 'cs_test_mask.png' for m in mdl.materials))

# collapse
hair = st.read('Item\\Default\\f1_hair01.nwmt', 'game')
cp = os.path.join(SCRATCH, 'hair.nwmt')
open(cp, 'wb').write(charswap.collapse_model(hair))
hm = load_model(cp)
check('hidden part: one invisible triangle per mesh', all(len(m.positions) == 3 and m.faces == [(0, 1, 2)] for m in hm.meshes))
check('hidden part keeps materials', [m.map('diffuse_map') for m in hm.materials] and b'<physics_list' not in open(cp, 'rb').read())

# =========================================================================== service: plan, build, remove
plan = svc.plan(('f1', 'm1'))
check('plan: animations per gender', len(plan['f1']['anims']) > 100 and len(plan['m1']['anims']) > 100,
      {g: len(p['anims']) for g, p in plan.items()})
check('plan: worn parts to hide', len(plan['f1']['hide']) > 300 and not any('body01' in n.lower() for n in plan['f1']['hide']))
check('plan: npc humans', plan['f1']['npc'] and plan['f1']['npc']['models'] and plan['m1']['npc']['anims'],
      plan['f1']['npc'])
prog = []
r = svc.build(src_glb, ('f1', 'm1'), progress=lambda d, t, m='': prog.append((d, t)))
check('swap written to the project', r['files'] > 1000 and svc.status() is not None, r.get('files'))
check('progress reaches the end', prog and prog[-1][0] == prog[-1][1], prog[-1:] if prog else None)
pf = st.project_files()
from narew.modfmt import entry_key  # noqa: E402
for n in ('Character\\F1\\f1.nwbt', 'Character\\M1\\m1.nwbt', 'Item\\Default\\f1_body01.nwmt',
          'Item\\Default\\m1_body01.nwmt', 'Character\\F1\\f1_common_run.nwat', 'Item\\Default\\f1_face01.nwmt',
          'npc\\human\\f1.nwbt'):
    check(f'project has {n}', entry_key(n) in pf)
tex = [n for k, (n, _) in pf.items() if k.startswith('item\\default\\cs_') and k.endswith('.png')]
check('textures written next to the body', len(tex) >= 3, tex)
st.project.put('Item\\Hair\\f1_hair_test_edit.nwmt', b'user file')         # unrelated user file survives
st.invalidate()
r2 = svc.remove()
pf = st.project_files()
check('remove deletes the swap', r2['removed'] == r['files'] and svc.status() is None, r2)
check('remove keeps unrelated files', list(pf) == [entry_key('Item\\Hair\\f1_hair_test_edit.nwmt')], list(pf)[:5])

# backups: a file the project had before comes back after remove
st.project.put('Item\\Default\\f1_face01.nwmt', b'my own face')
st.invalidate()
svc.build(src_glb, ('f1',), npcs=False)
check('swap overwrote the face', st.read('Item\\Default\\f1_face01.nwmt') != b'my own face')
svc.remove()
check('remove restores the earlier file', st.read('Item\\Default\\f1_face01.nwmt') == b'my own face')

# =========================================================================== agent tools
from studio.agent import cli  # noqa: E402
from studio.agent.context import AgentContext  # noqa: E402
from studio.agent.registry import call  # noqa: E402

cli._load_tools()
ctx = AgentContext(game=GAME, project=st.project.root)
ok, data, imgs = call(ctx, 'character_inspect', {'src': p_bin})
check('tool character_inspect', ok and data['bones'] == 2 and data['humanoid'] is False, data)
ok, data, imgs = call(ctx, 'character_swap', {'src': src_glb, 'dry_run': True, 'genders': ['f1']})
check('tool character_swap dry run + picture', ok and imgs and os.path.isfile(imgs[0]) and ctx.charswap.status() is None,
      data)
ok, data, imgs = call(ctx, 'character_swap', {'src': src_glb, 'genders': ['m1'], 'npcs': False, 'hide_parts': False})
check('tool character_swap', ok and data['files'] > 100 and imgs, data if not ok else '')
ok, data, _ = call(ctx, 'character_swap_status', {})
check('tool character_swap_status', ok and data and data['written'] > 100, data)
ok, data, _ = call(ctx, 'character_swap_remove', {})
check('tool character_swap_remove', ok and data['removed'] > 100, data)
ok, data, _ = call(ctx, 'character_swap', {'src': p_bin, 'genders': ['f1'], 'npcs': False, 'hide_parts': False,
                                           'dry_run': True})
check('no humanoid skeleton: still builds (rigid)', ok, data if not ok else '')
ok, data, _ = call(ctx, 'character_swap', {'src': os.path.join(WORK, 'nothing.fbx')})
check('missing file -> Korean error', not ok and '파일' in str(data), data)

if EXTRA:
    ok, data, _ = call(ctx, 'character_inspect', {'src': EXTRA})
    check('extra model inspected', ok and data.get('humanoid'), data if not ok else data.get('notes'))
    ok, data, imgs = call(ctx, 'character_swap', {'src': EXTRA, 'dry_run': True})
    check('extra model dry run', ok and imgs, data if not ok else '')
    if ok:
        print('   preview:', imgs[0])

failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed')
if failed:
    print('FAILED:', ', '.join(failed))
    sys.exit(1)
print('ALL OK')
