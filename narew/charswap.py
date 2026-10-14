"""Swap Islet Online's player character for a model from another tool or game.

The game builds a character from parts (``Item\\Default\\f1_body01.nwmt`` + face + hair + clothes ...)
skinned to one skeleton per gender (``Character\\F1\\f1.nwbt`` 31 bones, ``Character\\M1\\m1.nwbt`` 29
bones) and plays ``.nwat`` animations whose keys are local bone transforms.  A model with other
proportions cannot simply be skinned to that skeleton: the engine would rotate its elbows around the
chibi's elbows.  So the swap *retargets* the character instead:

1. the source model (any :mod:`narew.scene3d` format) is normalised: +Y up, facing the engine's -Z,
   scaled to the target height, feet on y = 0, then mirrored into the engine's left-handed space;
2. its humanoid bones are found by name and hierarchy (Biped, Mixamo, VRM/VRoid, Unity, UE, Rigify,
   MMD ...) and its arms/legs are re-posed into the Islet bind pose angles (linear blend skinning
   with the model's own weights);
3. a new skeleton keeps every Islet bone, id, name and bind *orientation*, but puts the joints where
   the model's joints are;
4. the model's skin weights are moved onto the Islet bones (fingers -> Finger0/Finger1, skirt bones
   -> pelvis blended into the thighs, hair -> head ...);
5. every animation of that skeleton keeps its rotations; bone translations become the new bone
   lengths and the root's motion is scaled by the hip height ratio (so crouches, jumps and walking
   bob fit the new legs).

Everything here works on bytes so the studio can write the results into a mod project.
"""
from __future__ import annotations

import copy
import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .gltf import rowmat_to_trs
from .nwmodel import Material, Mesh, Model, load_model, model_to_xml
from .nwxml import fmt_vec, parse_bytes, read_tm, serialize, detect_indent
from .scene3d import Scene, SceneError

SIDES = ('L', 'R')
S_MIRROR = np.diag([1.0, 1.0, -1.0, 1.0])


# =========================================================================== bone names

_GENERIC = {'bip', 'bip01', 'bip001', 'bip002', 'j', 'jnt', 'joint', 'def', 'mch', 'org', 'cc', 'base', 'b', 'bn',
            'bone', 'rig', 'c', 'mixamorig', 'valvebiped', 'sk', 'skel', 'ik', 'fk', 'jt', 'ctrl', 'deform', 'n'}
_LEFT = {'l', 'left', 'lft', 'lf', 'le'}
_RIGHT = {'r', 'right', 'rgt', 'rt', 'ri'}


def _tokens(name: str) -> list[str]:
    n = re.sub(r'^.*[:|]', '', name)                       # mixamorig:Hips, Armature|Hips
    n = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', n)           # LeftUpLeg -> Left Up Leg
    n = re.sub(r'(?<=[A-Za-z])(?=\d)', ' ', n)              # Spine1 -> Spine 1  (kept together below)
    return [t for t in re.split(r'[\s_.\-]+', n.lower()) if t]


def split_side(name: str) -> tuple[str | None, str]:
    """('L' | 'R' | None, core name without side and rig prefixes)."""
    raw = re.sub(r'^.*[:|]', '', name)
    side = None
    if raw.startswith('左') or '左' in raw:
        side, raw = 'L', raw.replace('左', '')
    elif raw.startswith('右') or '右' in raw:
        side, raw = 'R', raw.replace('右', '')
    toks = _tokens(raw)
    core = []
    for t in toks:
        if t in _LEFT and side is None:
            side = 'L'
            continue
        if t in _RIGHT and side is None:
            side = 'R'
            continue
        if t in _GENERIC and len(toks) > 1:
            continue
        core.append(t)
    if side is None:            # "upperarml" / "handr" style
        j = ''.join(core)
        m = re.match(r'^(.*(?:arm|hand|leg|foot|thigh|calf|shoulder|clavicle|toe|finger\d*|elbow|knee))([lr])$', j)
        if m:
            side, core = m.group(2).upper(), [m.group(1)]
    return side, ''.join(core)


_RULES = [
    ('nub', r'(nub|_?end|tip|先)$'),
    ('twist', r'twist|roll|捩|share'),
    ('thumb', r'thumb|finger0|親指'),
    ('index', r'index|finger1|人指|人差'),
    ('middle', r'middle|finger2|中指'),
    ('ring', r'ring|finger3|薬指'),
    ('pinky', r'pinky|little|finger4|小指'),
    ('toe', r'toe|ball|つま先'),
    ('foot', r'foot|ankle|足首'),
    ('calf', r'calf|shin|knee|lowerleg|leglow|lowleg|ひざ|膝'),
    ('thigh', r'thigh|upleg|upperleg|legup|^足d?$|腿'),
    ('hand', r'hand|wrist|手首|^手$'),
    ('forearm', r'forearm|lowerarm|armlow|elbow|ひじ|肘'),
    ('upperarm', r'upperarm|uparm|armup|^arm$|^腕$'),
    ('clavicle', r'clavicle|shoulder|collar|^肩$'),
    ('neck', r'neck|^首$'),
    ('head', r'head|^頭$'),
    ('spine', r'spine|chest|torso|abdomen|waist|上半身|stomach'),
    ('hips', r'hips?$|pelvis|下半身|^hip'),
    ('leg', r'^leg$'),
]


def classify(name: str) -> tuple[str | None, str | None]:
    side, core = split_side(name)
    if not core:
        return side, None
    if re.search(r'(nub|end|tip|top)$', core) and not re.search(r'^(spine|leg|hand)$', core):
        return side, 'nub'
    for kind, pat in _RULES[1:]:
        if re.search(pat, core):
            if kind == 'head' and re.search(r'headtop|head_?top', core):
                return side, 'nub'
            return side, kind
    return side, None


# =========================================================================== source rig

@dataclass
class Rig:
    slots: dict = field(default_factory=dict)        # slot -> bone index ('hips', 'upperarm.L', ...)
    spine: list = field(default_factory=list)        # hips (excl) -> neck (excl), bottom first
    fingers: dict = field(default_factory=dict)      # 'L' -> {'thumb': [chain], 'index': [...], ...}
    twist: dict = field(default_factory=dict)        # bone index -> target slot
    kinds: dict = field(default_factory=dict)        # bone index -> (side, kind)
    notes: list = field(default_factory=list)

    def get(self, slot):
        return self.slots.get(slot)


def _depth(sc: Scene, i: int) -> int:
    return len(sc.ancestors(i))


def _is_ancestor(sc: Scene, a: int, b: int) -> bool:
    return a in sc.ancestors(b)


def _lca(sc: Scene, a: int, b: int) -> int | None:
    sa = [a] + sc.ancestors(a)
    sb = set([b] + sc.ancestors(b))
    for x in sa:
        if x in sb:
            return x
    return None


def detect_rig(sc: Scene) -> Rig | None:
    """Find the humanoid bones of a source skeleton; None if there is no usable skeleton."""
    if len(sc.bones) < 5:
        return None
    rig = Rig()
    kinds = {i: classify(b.name) for i, b in enumerate(sc.bones)}
    rig.kinds = kinds
    # bones that actually deform something (or have deforming descendants) are preferred
    used = set()
    for m in sc.meshes:
        if m.joints is not None:
            used |= set(np.unique(m.joints[m.weights > 0]).tolist())

    def pick(kind, side=None, among=None):
        c = [i for i, (s, k) in kinds.items() if k == kind and (side is None or s == side)
             and (among is None or i in among)]
        if not c:
            return None
        c.sort(key=lambda i: (0 if (i in used or any(d in used for d in sc.descendants(i))) else 1,
                              _depth(sc, i)))
        return c[0]

    def skip_twist_up(i):
        p = sc.bones[i].parent
        while p >= 0 and kinds[p][1] in ('twist', 'nub'):
            p = sc.bones[p].parent
        return p if p >= 0 else None

    for s in SIDES:
        hand = pick('hand', s)
        if hand is not None:
            fore = pick('forearm', s, among=sc.ancestors(hand)) or skip_twist_up(hand)
            upper = pick('upperarm', s, among=sc.ancestors(fore)) if fore is not None else None
            if upper is None and fore is not None:
                upper = skip_twist_up(fore)
            clav = pick('clavicle', s, among=sc.ancestors(upper)) if upper is not None else None
            rig.slots.update({f'hand.{s}': hand, f'forearm.{s}': fore, f'upperarm.{s}': upper})
            if clav is not None:
                rig.slots[f'clavicle.{s}'] = clav
        foot = pick('foot', s)
        if foot is not None:
            calf = pick('calf', s, among=sc.ancestors(foot))
            if calf is None:
                legs = [a for a in sc.ancestors(foot) if kinds[a][1] == 'leg' and kinds[a][0] == s]
                calf = legs[0] if legs else skip_twist_up(foot)
            thigh = pick('thigh', s, among=sc.ancestors(calf)) if calf is not None else None
            if thigh is None and calf is not None:
                thigh = skip_twist_up(calf)
            rig.slots.update({f'foot.{s}': foot, f'calf.{s}': calf, f'thigh.{s}': thigh})
            toe = pick('toe', s, among=sc.descendants(foot))
            if toe is None:
                kids = [c for c in sc.children(foot) if kinds[c][1] != 'nub']
                toe = kids[0] if kids else None
            if toe is not None:
                rig.slots[f'toe.{s}'] = toe
    head = pick('head')
    if head is not None:
        rig.slots['head'] = head
        neck = pick('neck', among=sc.ancestors(head))
        rig.slots['neck'] = neck if neck is not None else skip_twist_up(head)
    tl, tr = rig.slots.get('thigh.L'), rig.slots.get('thigh.R')
    hips = pick('hips')
    if tl is not None and tr is not None:
        lca = _lca(sc, tl, tr)
        if hips is None or not (_is_ancestor(sc, hips, tl) and _is_ancestor(sc, hips, tr)):
            hips = lca
    if hips is not None:
        rig.slots['hips'] = hips
    for slot, b in sc.humanoid.items():          # VRM humanoid map wins over names
        rig.slots[slot] = b
    tl, tr = rig.slots.get('thigh.L'), rig.slots.get('thigh.R')
    hips = rig.slots.get('hips')
    need = ['hips', 'head', 'hand.L', 'hand.R', 'foot.L', 'foot.R', 'upperarm.L', 'upperarm.R',
            'forearm.L', 'forearm.R', 'thigh.L', 'thigh.R', 'calf.L', 'calf.R']
    missing = [k for k in need if rig.slots.get(k) is None]
    if missing:
        rig.notes.append('사람 뼈대를 찾지 못함: ' + ', '.join(missing))
        return None
    # spine chain: ancestors of the neck below the common ancestor of neck and legs
    neck = rig.slots.get('neck')
    if neck is None:
        neck = rig.slots['neck'] = sc.bones[rig.slots['head']].parent
    top = _lca(sc, neck, tl)
    chain = []
    p = sc.bones[neck].parent
    while p is not None and p >= 0 and p != top and p != hips:
        chain.append(p)
        p = sc.bones[p].parent
    rig.spine = [b for b in reversed(chain) if kinds[b][1] not in ('twist', 'nub')]
    if not rig.spine and top is not None and top != hips:
        rig.spine = [top]
    # fingers
    for s in SIDES:
        hand = rig.slots[f'hand.{s}']
        f = {}
        for d in sc.descendants(hand):
            side, k = kinds[d]
            if k in ('thumb', 'index', 'middle', 'ring', 'pinky') and side in (s, None):
                f.setdefault(k, []).append(d)
        if not f:                       # unnamed fingers: each child chain of the hand
            kids = sorted(sc.children(hand), key=lambda c: sc.bones[c].head[2])
            names = ['thumb', 'index', 'middle', 'ring', 'pinky']
            for n, c in zip(names, kids):
                f[n] = [c] + [d for d in sc.descendants(c)]
        for k in f:
            f[k].sort(key=lambda i: _depth(sc, i))
        rig.fingers[s] = f
    # twist bones
    for i, (side, k) in kinds.items():
        if k != 'twist':
            continue
        anc = sc.ancestors(i)
        core = split_side(sc.bones[i].name)[1]
        for s in SIDES:
            in_arm = rig.slots.get(f'upperarm.{s}') in anc
            if in_arm and re.search(r'fore|lower|elbow|hand|wrist|手|ひじ', core):
                rig.twist[i] = f'foretwist.{s}'         # Biped ForeTwist hangs under the upper arm
                break
            if rig.slots.get(f'forearm.{s}') in anc or rig.slots.get(f'hand.{s}') == sc.bones[i].parent:
                rig.twist[i] = f'foretwist.{s}'
                break
            if in_arm:
                rig.twist[i] = f'upperarm.{s}'
                break
            for part in ('calf', 'thigh'):
                if rig.slots.get(f'{part}.{s}') in anc + [sc.bones[i].parent]:
                    rig.twist[i] = f'{part}.{s}'
                    break
            if i in rig.twist:
                break
    return rig


# =========================================================================== Islet target

def islet_slot(name: str) -> str | None:
    if re.match(r'^Bip0*1$', name):
        return 'root'
    m = re.match(r'^Bip0*1 (?:(L|R) )?(.+)$', name)
    if not m:
        return None
    s, part = m.group(1), m.group(2)
    table = {'Pelvis': 'hips', 'Spine': 'spine0', 'Spine1': 'spine1', 'Spine2': 'spine2', 'Neck': 'neck',
             'Head': 'head', 'Footsteps': 'footsteps', 'Xtra01': 'skirt.F', 'Xtra02': 'skirt.B',
             'Clavicle': 'clavicle', 'UpperArm': 'upperarm', 'Forearm': 'forearm', 'Hand': 'hand',
             'Finger0': 'thumb', 'Finger1': 'fingers', 'ForeTwist': 'foretwist', 'Thigh': 'thigh',
             'Calf': 'calf', 'Foot': 'foot', 'Toe0': 'toe'}
    base = table.get(part)
    if base is None:
        return None
    return f'{base}.{s}' if s else base


@dataclass
class TBone:
    id: int
    name: str
    parent: int
    world: np.ndarray        # column-vector 4x4 bind world (engine space)
    slot: str | None


def _col(rows) -> np.ndarray:
    return np.array(rows, dtype=np.float64).T


def _rows(m: np.ndarray) -> list:
    return m.T.tolist()


class Target:
    """An Islet skeleton (.nwbt bytes) to retarget onto."""

    def __init__(self, nwbt: bytes):
        self.data = nwbt
        self.root = parse_bytes(nwbt)
        bl = self.root.find('bonelist')
        if bl is None:
            raise SceneError('스켈레톤(.nwbt)에 bonelist가 없습니다')
        self.bones: list[TBone] = []
        for b in bl:
            p = b.find('parent')
            pid = int(p.get('id', -1)) if p is not None else -1
            tm = read_tm(b.find('transform'))
            self.bones.append(TBone(int(b.get('id')), b.get('name', ''), pid, _col(tm), islet_slot(b.get('name', ''))))
        self.bones.sort(key=lambda t: t.id)
        self.by_slot = {t.slot: t.id for t in self.bones if t.slot}
        for need in ('root', 'hips', 'head', 'hand.L', 'hand.R', 'foot.L', 'foot.R'):
            if need not in self.by_slot:
                raise SceneError(f'아일렛 스켈레톤이 아닙니다 ({need} 뼈 없음)')

    def pos(self, slot: str) -> np.ndarray | None:
        i = self.by_slot.get(slot)
        return None if i is None else self.bones[i].world[:3, 3].copy()

    def dir(self, a: str, b: str) -> np.ndarray | None:
        pa, pb = self.pos(a), self.pos(b)
        if pa is None or pb is None:
            return None
        d = pb - pa
        n = np.linalg.norm(d)
        return d / n if n > 1e-9 else None


# =========================================================================== math

def _rot_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.999999:
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def _about(pivot: np.ndarray, r3: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = r3
    m[:3, 3] = pivot - r3 @ pivot
    return m


def _normalize_rows(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.where(n > 1e-12, n, 1.0)


def _snap_axis(v: np.ndarray) -> np.ndarray:
    k = int(np.argmax(np.abs(v)))
    out = np.zeros(3)
    out[k] = 1.0 if v[k] > 0 else -1.0
    return out


# =========================================================================== normalising

def normalize_scene(sc: Scene, rig: Rig | None, height: float, turn: float = 0.0) -> dict:
    """Scene (RH, any facing) -> engine space (LH, +Y up, facing -Z, left = +X), feet on y=0,
    hips at x = z = 0, ``height`` tall.  Returns a report."""
    rep = {}
    if rig is not None:
        B = lambda s: sc.bones[rig.slots[s]].head
        up = B('head') - B('hips')
        up_axis = _snap_axis(up)
        if up_axis[1] != 1.0:
            m = np.eye(4)
            m[:3, :3] = _rot_between(up_axis, np.array([0.0, 1.0, 0.0]))
            sc.transform(m)
            rep['up_fixed'] = True
        left = (B('thigh.L') - B('thigh.R')) + (B('upperarm.L') - B('upperarm.R'))
        left[1] = 0.0
        if np.linalg.norm(left) > 1e-9:
            la = _snap_axis(left)
            if la[0] != 1.0:
                m = np.eye(4)
                m[:3, :3] = _rot_between(la, np.array([1.0, 0.0, 0.0]))
                if la[0] == -1.0:       # 180 degrees about Y, not a flip
                    m[:3, :3] = np.diag([-1.0, 1.0, -1.0])
                sc.transform(m)
                rep['turned'] = True
    if turn:
        m = np.eye(4)
        m[:3, :3] = _rodrigues(np.array([0.0, 1.0, 0.0]), float(turn))
        sc.transform(m)
        rep['turn'] = float(turn)
    lo, hi = sc.bounds()
    h = float(hi[1] - lo[1])
    if h <= 1e-9:
        raise SceneError('모델 높이가 0입니다')
    s = height / h
    if rig is not None:
        c = sc.bones[rig.slots['hips']].head
        cx, cz = float(c[0]), float(c[2])
    else:
        cx, cz = float(lo[0] + hi[0]) / 2, float(lo[2] + hi[2]) / 2
    m = np.diag([s, s, s, 1.0])
    m[:3, 3] = [-cx * s, -lo[1] * s, -cz * s]
    sc.transform(m)
    sc.transform(S_MIRROR)          # right-handed facing +Z -> engine left-handed facing -Z
    rep['scale'] = s
    rep['source_height'] = h
    return rep


# =========================================================================== re-posing

ALIGN = [  # (source slot, source end, target from, target to)
    ('clavicle', 'upperarm', 'clavicle', 'upperarm'),
    ('upperarm', 'forearm', 'upperarm', 'forearm'),
    ('forearm', 'hand', 'forearm', 'hand'),
    ('hand', '@fingers', 'hand', 'fingers'),
    ('thigh', 'calf', 'thigh', 'calf'),
    ('calf', 'foot', 'calf', 'foot'),
]


def _finger_root(sc: Scene, rig: Rig, s: str) -> np.ndarray | None:
    f = rig.fingers.get(s, {})
    for k in ('middle', 'index', 'ring', 'pinky'):
        if f.get(k):
            return sc.bones[f[k][0]].head
    return None


def _rodrigues(axis: np.ndarray, deg: float) -> np.ndarray:
    a = axis / np.linalg.norm(axis)
    t = math.radians(deg)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * k + (1 - math.cos(t)) * k @ k


def _toward(d: np.ndarray, goal: np.ndarray, deg: float) -> np.ndarray:
    """Rotation turning direction ``d`` by ``deg`` degrees toward ``goal`` (in their plane)."""
    axis = np.cross(d, goal)
    if np.linalg.norm(axis) < 1e-9 or abs(deg) < 1e-9:
        return np.eye(3)
    return _rodrigues(axis, deg)


def auto_leg_angle(sc: Scene, rig: Rig, tgt: Target, stance: float = 7.0) -> float:
    """Degrees to splay the model's legs so the Islet stance (feet ~7 degrees inward under the
    chibi's wide hips) keeps the feet apart in proportion instead of crossing them."""
    try:
        hx_t = abs(tgt.pos('thigh.L')[0] - tgt.pos('thigh.R')[0]) / 2
        L_t = np.linalg.norm(tgt.pos('foot.L') - tgt.pos('thigh.L'))
        B = lambda k: sc.bones[rig.slots[k]].head
        hx_s = abs(B('thigh.L')[0] - B('thigh.R')[0]) / 2
        L_s = np.linalg.norm(B('foot.L') - B('thigh.L'))
    except (KeyError, TypeError):
        return 0.0
    if min(hx_t, L_t, hx_s, L_s) < 1e-6:
        return 0.0
    k = (L_t * hx_s) / (hx_t * L_s)
    s2 = max(-1.0, min(1.0, math.sin(math.radians(stance)) * k))
    return float(max(0.0, min(12.0, stance - math.degrees(math.asin(s2)))))


def repose(sc: Scene, rig: Rig, tgt: Target, arm_angle: float = 12.0, leg_angle: float | None = None) -> np.ndarray:
    """Rotate the arms and legs into the target's bind pose angles.  Returns the per-bone
    world deltas (n, 4, 4) applied to the mesh (linear blend skinning).

    ``arm_angle``: the arms are posed this many degrees closer to the body than the Islet bind
    pose (the chibi holds its arms away from its round belly; a slim model would look like it
    is flapping).  ``leg_angle``: legs are splayed outward by this much (None = from the hip
    width / leg length ratio), so the chibi's knock-kneed stance does not cross slim legs.
    Feet keep their original orientation (soles stay flat)."""
    if leg_angle is None:
        leg_angle = auto_leg_angle(sc, rig, tgt)
    n = len(sc.bones)
    D = np.repeat(np.eye(4)[None], n, axis=0)
    align = {}
    down = np.array([0.0, -1.0, 0.0])
    for s in SIDES:
        out = np.array([1.0 if s == 'L' else -1.0, 0.0, 0.0])
        ua = tgt.dir(f'upperarm.{s}', f'forearm.{s}')
        R_arm = _toward(ua, down, arm_angle) if ua is not None else np.eye(3)
        th = tgt.dir(f'thigh.{s}', f'calf.{s}')
        R_leg = _toward(th, out, leg_angle) if th is not None else np.eye(3)
        for a, b, ta, tb in ALIGN:
            src = rig.slots.get(f'{a}.{s}')
            if src is None:
                continue
            if b == '@fingers':
                end = _finger_root(sc, rig, s)
            else:
                e = rig.slots.get(f'{b}.{s}')
                end = sc.bones[e].head if e is not None else None
            d = tgt.dir(f'{ta}.{s}', f'{tb}.{s}')
            if end is None or d is None:
                continue
            if a in ('upperarm', 'forearm', 'hand'):
                d = R_arm @ d
            elif a in ('thigh', 'calf'):
                d = R_leg @ d
            align[src] = (end, d)
    feet = {rig.slots.get(f'foot.{s}') for s in SIDES} - {None}
    order = sorted(range(n), key=lambda i: _depth(sc, i))
    for i in order:
        p = sc.bones[i].parent
        D[i] = D[p] if p >= 0 else np.eye(4)
        if i in feet:
            # keep the foot's world orientation: only follow the ankle's new position
            j0 = sc.bones[i].head
            j = (D[i] @ np.append(j0, 1.0))[:3]
            D[i] = np.eye(4)
            D[i][:3, 3] = j - j0
        if i in align:
            end, d = align[i]
            j = (D[i] @ np.append(sc.bones[i].head, 1.0))[:3]
            e = (D[i] @ np.append(end, 1.0))[:3]
            cur = e - j
            if np.linalg.norm(cur) > 1e-9:
                D[i] = _about(j, _rot_between(cur, d)) @ D[i]
    # apply to meshes
    for m in sc.meshes:
        if m.joints is None:
            continue
        w = m.weights / np.maximum(m.weights.sum(axis=1, keepdims=True), 1e-12)
        M = np.einsum('vk,vkij->vij', w, D[m.joints])
        p4 = np.concatenate([m.positions, np.ones((len(m.positions), 1))], axis=1)
        m.positions = np.einsum('vij,vj->vi', M, p4)[:, :3]
        if m.normals is not None:
            m.normals = _normalize_rows(np.einsum('vij,vj->vi', M[:, :3, :3], m.normals))
    for i, b in enumerate(sc.bones):
        b.bind = D[i] @ b.bind
    return D


# =========================================================================== build

@dataclass
class Character:
    skeleton: bytes                       # new .nwbt
    body: bytes                           # new body .nwmt
    textures: dict                        # file name -> png bytes
    plan: dict                            # animation retarget plan
    report: dict
    bones_new: list = field(default_factory=list)   # column 4x4 per bone id (debug/tests)


def _png(data: bytes | None, max_side: int = 2048) -> bytes:
    from PIL import Image
    if data is None:
        im = Image.new('RGBA', (4, 4), (255, 255, 255, 255))
    else:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert('RGBA')
        if max(im.size) > max_side:
            k = max_side / max(im.size)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, 'PNG', optimize=True)
    return out.getvalue()


def _solid_png(rgb=(0, 0, 0)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (4, 4), rgb).save(out, 'PNG')
    return out.getvalue()


def _skin_targets(sc: Scene, rig: Rig | None, tgt: Target) -> dict:
    """source bone -> target slot or ('skirt',) for hip children that are neither legs nor spine."""
    if rig is None:
        return {}
    out = {}
    sl = rig.slots
    for s in SIDES:
        for part in ('clavicle', 'upperarm', 'forearm', 'hand', 'thigh', 'calf', 'foot', 'toe'):
            b = sl.get(f'{part}.{s}')
            if b is not None:
                out[b] = f'{part}.{s}'
        f = rig.fingers.get(s, {})
        for k, chain in f.items():
            for b in chain:
                out[b] = f'thumb.{s}' if k == 'thumb' else f'fingers.{s}'
    for b, slot in rig.twist.items():
        out[b] = slot if slot in tgt.by_slot else slot.replace('foretwist', 'forearm')
    out[sl['hips']] = 'hips'
    if sl.get('neck') is not None:
        out[sl['neck']] = 'neck'
    out[sl['head']] = 'head'
    for k, b in enumerate(rig.spine):
        out[b] = 'spine0' if k == 0 else ('spine1' if 'spine1' in tgt.by_slot else 'spine0')
    chains = set(out)
    hips = sl['hips']
    # everything else follows its nearest mapped ancestor; hip children that are not legs or
    # spine (skirts, tails, coat tails) are blended between pelvis and thighs
    for i in range(len(sc.bones)):
        if i in out:
            continue
        anc = sc.ancestors(i)
        slot = None
        for a in anc:
            if a in out:
                slot = out[a]
                if a == hips:
                    slot = 'skirt'
                break
        if slot is None:
            # above the hips (armature root, centre bone) -> hips
            slot = 'hips'
        out[i] = slot
    del chains
    return out


def _vertex_weights(sc: Scene, m, bone_slot: dict, tgt: Target, newpos: dict) -> tuple[np.ndarray, np.ndarray]:
    """(N, 4) target bone ids and weights for one mesh."""
    nv = len(m.positions)
    nb = len(tgt.bones)
    W = np.zeros((nv, nb))
    hips = tgt.by_slot['hips']
    if m.joints is None:
        # unskinned mesh: nearest target joint (with its parent) by distance
        joints = np.array([newpos[b.id] for b in tgt.bones])
        valid = np.array([b.slot not in (None, 'root', 'footsteps') for b in tgt.bones])
        d = np.linalg.norm(m.positions[:, None, :] - joints[None], axis=2)
        d[:, ~valid] = np.inf
        k = np.argmin(d, axis=1)
        W[np.arange(nv), k] = 1.0
    else:
        thigh = {s: tgt.by_slot.get(f'thigh.{s}') for s in SIDES}
        calf_y = {s: newpos[tgt.by_slot[f'calf.{s}']][1] if f'calf.{s}' in tgt.by_slot else 0.0 for s in SIDES}
        hip_y = newpos[hips][1]
        tx = abs(newpos[thigh['L']][0]) if thigh['L'] is not None else 0.1
        for k in range(m.joints.shape[1]):
            j = m.joints[:, k]
            w = m.weights[:, k]
            for sb in np.unique(j[w > 0]):
                sel = (j == sb) & (w > 0)
                slot = bone_slot.get(int(sb), 'hips')
                if slot == 'skirt' and thigh['L'] is not None and thigh['R'] is not None:
                    p = m.positions[sel]
                    knee = (calf_y['L'] + calf_y['R']) / 2
                    t = np.clip((hip_y - p[:, 1]) / max(hip_y - knee, 1e-6), 0.0, 1.0)
                    follow = 0.65 * t
                    left = np.clip(0.5 + 0.5 * p[:, 0] / max(tx, 1e-6), 0.0, 1.0)
                    idx = np.nonzero(sel)[0]
                    W[idx, hips] += w[sel] * (1 - follow)
                    W[idx, thigh['L']] += w[sel] * follow * left
                    W[idx, thigh['R']] += w[sel] * follow * (1 - left)
                    continue
                if slot == 'skirt':
                    slot = 'hips'
                tid = tgt.by_slot.get(slot)
                if tid is None and slot.startswith('foretwist'):
                    tid = tgt.by_slot.get(slot.replace('foretwist', 'forearm'))
                if tid is None:
                    tid = hips
                W[sel, tid] += w[sel]
    # never weight the root (the engine keeps a parentless bone's skin matrix at identity)
    root = tgt.by_slot['root']
    W[:, hips] += W[:, root]
    W[:, root] = 0.0
    order = np.argsort(-W, axis=1)[:, :4]
    ww = np.take_along_axis(W, order, axis=1)
    tot = ww.sum(axis=1, keepdims=True)
    empty = tot[:, 0] < 1e-9
    ww = np.where(tot > 1e-9, ww / np.maximum(tot, 1e-12), 0.0)
    order[empty, 0] = hips
    ww[empty, 0] = 1.0
    # unused influence slots point at the first bone with weight 0
    for c in range(1, 4):
        z = ww[:, c] <= 1e-6
        order[z, c] = order[z, 0]
        ww[z, c] = 0.0
    # the engine computes the 4th weight as 1 - (w0 + w1 + w2): make the first three exact
    ww[:, 3] = 1.0 - ww[:, :3].sum(axis=1)
    ww[:, 3] = np.where(ww[:, 3] < 1e-6, 0.0, ww[:, 3])
    return order, ww


def _tangents(pos, nrm, uv, faces) -> np.ndarray:
    tan = np.zeros_like(pos)
    bit = np.zeros_like(pos)
    p0, p1, p2 = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
    t0, t1, t2 = uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    du1, dv1 = t1[:, 0] - t0[:, 0], t1[:, 1] - t0[:, 1]
    du2, dv2 = t2[:, 0] - t0[:, 0], t2[:, 1] - t0[:, 1]
    r = du1 * dv2 - du2 * dv1
    ok = np.abs(r) > 1e-20
    r = np.where(ok, 1.0 / np.where(ok, r, 1.0), 0.0)
    sdir = (e1 * dv2[:, None] - e2 * dv1[:, None]) * r[:, None]
    tdir = (e2 * du1[:, None] - e1 * du2[:, None]) * r[:, None]
    for k in range(3):
        np.add.at(tan, faces[:, k], sdir)
        np.add.at(bit, faces[:, k], tdir)
    t = tan - nrm * np.sum(tan * nrm, axis=1, keepdims=True)
    ln = np.linalg.norm(t, axis=1)
    bad = ln < 1e-12
    if bad.any():
        alt = np.cross(nrm[bad], np.array([0.0, 1.0, 0.0]))
        small = np.linalg.norm(alt, axis=1) < 1e-6
        alt[small] = np.cross(nrm[bad][small], np.array([1.0, 0.0, 0.0]))
        t[bad] = alt
    t = _normalize_rows(t)
    w = np.where(np.sum(np.cross(nrm, t) * bit, axis=1) < 0, -1.0, 1.0)
    return np.concatenate([t, w[:, None]], axis=1)


def _face_normals(pos, faces) -> np.ndarray:
    acc = np.zeros_like(pos)
    # engine faces are clockwise from the front (left-handed): normal = (c-a) x (b-a)
    n = np.cross(pos[faces[:, 2]] - pos[faces[:, 0]], pos[faces[:, 1]] - pos[faces[:, 0]])
    for k in range(3):
        np.add.at(acc, faces[:, k], n)
    return _normalize_rows(acc)


def _material_element(mid: int, name: str, diffuse: str, spec: str, mask: str) -> ET.Element:
    e = ET.Element('material', {'id': str(mid), 'name': name})
    ET.SubElement(e, 'shader', {'file': 'DefaultSkinMesh.fx', 'technique': 'RenderDefault'})
    ET.SubElement(e, 'color', {'ambient': '0xff808080', 'diffuse': '0xffffffff', 'specular': '0xffffffff',
                               'result': '0xffffffff'})
    ET.SubElement(e, 'specular', {'power': '1', 'gloss': '10'})
    for tag, v in (('diffuse_map', diffuse), ('normal_map', ''), ('specular_map', spec), ('mask_map', mask)):
        x = ET.SubElement(e, tag)
        x.text = v
    return e


def _set_tm(transform: ET.Element, rows) -> None:
    for tm in transform.findall('tm'):
        r = int(tm.get('row'))
        tm.set('value', fmt_vec(rows[r], 6))


def _quat_fix(q):
    return tuple(0.0 if abs(c) < 5e-5 else c for c in q)


def build(sc: Scene, rig: Rig | None, tgt: Target, body_template: bytes, tex_prefix: str,
          gender: str = '') -> Character:
    """``sc`` must already be normalised (:func:`normalize_scene`) and re-posed (:func:`repose`)."""
    old = {b.id: b.world[:3, 3].copy() for b in tgt.bones}
    newpos: dict[int, np.ndarray] = {}
    by = tgt.by_slot
    srcpos = {}
    if rig is not None:
        for slot, b in rig.slots.items():
            if b is not None:
                srcpos[slot] = sc.bones[b].head.copy()
        if rig.spine:
            srcpos['spine0'] = sc.bones[rig.spine[0]].head.copy()
            if len(rig.spine) >= 2:
                srcpos['spine1'] = sc.bones[rig.spine[1]].head.copy()
        for s in SIDES:
            f = rig.fingers.get(s, {})
            if f.get('thumb'):
                srcpos[f'thumb.{s}'] = sc.bones[f['thumb'][0]].head.copy()
            r = _finger_root(sc, rig, s)
            if r is not None:
                srcpos[f'fingers.{s}'] = r.copy()
    # body scale from the hip height
    hips_old = old[by['hips']]
    if 'hips' in srcpos:
        s_root = float(srcpos['hips'][1] / max(hips_old[1], 1e-6))
    else:
        lo, hi = sc.bounds()
        s_root = float((hi[1] - lo[1]) / max(max(p[1] for p in old.values()), 1e-6))
    report = {'hip_scale': round(s_root, 4)}

    def seg_scale(slot_a, slot_b, default):
        if slot_a in srcpos and slot_b in srcpos and slot_a in by and slot_b in by:
            o = np.linalg.norm(old[by[slot_b]] - old[by[slot_a]])
            nn = np.linalg.norm(srcpos[slot_b] - srcpos[slot_a])
            if o > 1e-6:
                return float(nn / o)
        return default

    s_hand = {s: seg_scale(f'forearm.{s}', f'hand.{s}', s_root) for s in SIDES}
    s_foot = {s: seg_scale(f'calf.{s}', f'foot.{s}', s_root) for s in SIDES}
    # joint positions, parents first
    order = []
    seen = set()

    def visit(i):
        if i in seen:
            return
        p = tgt.bones[i].parent
        if p >= 0:
            visit(p)
        seen.add(i)
        order.append(i)

    for b in tgt.bones:
        visit(b.id)
    local_scale = {}
    for i in order:
        b = tgt.bones[i]
        slot = b.slot or ''
        side = slot.split('.')[1] if '.' in slot else None
        if slot == 'root':
            p = srcpos.get('hips', old[i] * s_root).copy()
            newpos[i] = p
            continue
        if slot == 'footsteps':
            r = newpos[by['root']]
            newpos[i] = np.array([r[0], old[i][1] * s_root, r[2]])
            continue
        if slot.startswith('foretwist') and f'forearm.{side}' in by:
            newpos[i] = None            # = the forearm joint, set below
            continue
        if slot in srcpos:
            newpos[i] = srcpos[slot].copy()
            continue
        if slot == 'spine1' and 'spine0' in srcpos and 'neck' in srcpos:
            newpos[i] = srcpos['spine0'] + (srcpos['neck'] - srcpos['spine0']) * 0.5
            continue
        # unmapped: keep the old offset from the parent, scaled
        p = b.parent
        if p < 0:
            newpos[i] = old[i] * s_root
            continue
        k = s_root
        if slot.startswith(('thumb', 'fingers')) and side:
            k = s_hand[side]
        elif slot.startswith('toe') and side:
            k = s_foot[side]
        newpos[i] = newpos[p] + (old[i] - old[p]) * k
        local_scale[i] = k
    # ForeTwist copies the forearm joint when it was not set above
    for t in tgt.bones:
        if t.slot and t.slot.startswith('foretwist') and newpos.get(t.id) is None:
            s = t.slot.split('.')[1]
            newpos[t.id] = newpos[by[f'forearm.{s}']].copy()

    # new bind matrices: old orientation, new joint
    new_world = {}
    for b in tgt.bones:
        m = b.world.copy()
        m[:3, 3] = newpos[b.id]
        new_world[b.id] = m

    # ---------------------------------------------------------------- skeleton xml
    root = copy.deepcopy(tgt.root)
    bl = root.find('bonelist')
    locals_new = {}
    locals_old = {}
    for be in bl:
        i = int(be.get('id'))
        b = tgt.bones[i]
        _set_tm(be.find('transform'), _rows(new_world[i]))
        if b.parent >= 0:
            loc = np.linalg.inv(new_world[b.parent]) @ new_world[i]
            loc_old = np.linalg.inv(b_world(tgt, b.parent)) @ b.world
        else:
            loc = new_world[i]
            loc_old = b.world
        locals_new[i] = loc
        locals_old[i] = loc_old
        ik = be.find('init_key')
        if ik is not None:
            t, q, s = rowmat_to_trs(_rows(loc))
            ik.set('pos', fmt_vec(t, 4))
            ik.set('rot', fmt_vec(_quat_fix(q), 4))
            ik.set('scale', fmt_vec(s, 4))
    hl = root.find('helper_list')
    if hl is not None:
        _update_helpers(hl, tgt, new_world, s_root, s_hand)
    decl = _declaration(tgt.data)
    skel_bytes = serialize(list(root), declaration=decl, wrap_root=root.get('wrapped') == 'root',
                           indent=detect_indent(tgt.data))

    # ---------------------------------------------------------------- body model
    bone_slot = _skin_targets(sc, rig, tgt)
    tmpl = load_model_bytes(body_template)
    textures = {}
    materials = []
    meshes = []
    mat_files = {}
    spec_name = f'{tex_prefix}_spec.png'
    mask_name = f'{tex_prefix}_mask.png'
    textures[spec_name] = _solid_png((0, 0, 0))
    textures[mask_name] = _solid_png((0, 0, 0))
    used_names = set()
    for mi, sm in enumerate(sc.materials):
        key = (sm.image_name or '', len(sm.image or b''), hash(sm.image or b''))
        if key not in mat_files:
            fn = f'{tex_prefix}.png' if not mat_files else f'{tex_prefix}_{len(mat_files)}.png'
            textures[fn] = _png(sm.image) if sm.image is not None else _solid_tint(sm.color)
            mat_files[key] = fn
        fn = mat_files[key]
        name = re.sub(r'[^\w\- ]', '_', sm.name)[:40] or f'mat{mi}'
        while name in used_names:
            name += '_'
        used_names.add(name)
        materials.append(Material(mi, name, _material_element(mi, name, fn, spec_name, mask_name)))
    if not materials:
        fn = f'{tex_prefix}.png'
        textures[fn] = _solid_tint((0.8, 0.8, 0.8, 1.0))
        materials.append(Material(0, 'default', _material_element(0, 'default', fn, spec_name, mask_name)))
    mesh_id = 0
    for sm in sc.meshes:
        ids, ww = _vertex_weights(sc, sm, bone_slot, tgt, newpos)
        fm = sm.face_material if sm.face_material is not None else np.zeros(len(sm.faces), dtype=np.int64)
        for mat in sorted(set(fm.tolist())):
            fsel = sm.faces[fm == mat]
            if len(fsel) == 0:
                continue
            for part in _split_faces(fsel, 65000):
                used, inv = np.unique(part, return_inverse=True)
                faces = inv.reshape(-1, 3)
                pos = sm.positions[used]
                nrm = sm.normals[used] if sm.normals is not None else None
                if nrm is None or not np.isfinite(nrm).all():
                    nrm = _face_normals(pos, faces)
                uv = sm.uvs[used] if sm.uvs is not None else np.zeros((len(used), 2))
                tan = _tangents(pos, nrm, uv, faces)
                skin = [[(int(ids[v, c]), float(ww[v, c])) for c in range(4)] for v in used]
                mesh = Mesh('skinmesh', mesh_id, (re.sub(r'[^\w\-]', '_', sm.name) or 'mesh')[:40] + (f'_{mesh_id}' if mesh_id else ''),
                            material=int(mat) if mat >= 0 else 0)
                mesh.positions = [tuple(map(float, p)) for p in pos]
                mesh.normals = [tuple(map(float, p)) for p in nrm]
                mesh.tangents = [tuple(map(float, p)) for p in tan]
                mesh.uvs = [tuple(map(float, p)) for p in uv]
                mesh.skin = skin
                mesh.faces = [tuple(map(int, f)) for f in faces]
                mesh.index_attrs = {'b32bit': '0'}
                mesh.bbox_from_file = False
                mesh.use_bone = len({b for infl in skin for b, w in infl if w > 0} | {infl[0][0] for infl in skin})
                meshes.append(mesh)
                mesh_id += 1
    # helpers of the body model
    sections = []
    for tag, e in tmpl.sections:
        if tag in ('mesh_list', 'skinmesh_list', 'material_list', 'header', 'bound_box'):
            sections.append((tag, None))
        elif tag == 'helper_list':
            h = copy.deepcopy(e)
            _update_helpers(h, tgt, new_world, s_root, s_hand)
            sections.append((tag, h))
        elif tag == 'special_bone_list':
            sections.append((tag, ET.Element('special_bone_list', {'count': '0'})))
        elif tag == 'physics_list':
            continue
        else:
            sections.append((tag, e))
    if not any(t == 'skinmesh_list' for t, _ in sections):
        sections.insert(2, ('skinmesh_list', None))
    header = copy.deepcopy(tmpl.header)
    if header is not None:
        header.set('special_bone', '0')
        header.set('physics', '0')
    model = Model(None, header, materials, meshes, sections, {}, '    ')
    body_bytes = model_to_xml(model)

    # ---------------------------------------------------------------- animation plan
    plan = {'root': by['root'], 's_root': s_root, 'bones': {}}
    for b in tgt.bones:
        if b.parent < 0:
            continue
        plan['bones'][b.id] = (locals_old[b.id][:3, 3].copy(), locals_new[b.id][:3, 3].copy(),
                               local_scale.get(b.id, s_root) if b.slot != 'footsteps' else s_root,
                               b.slot == 'footsteps')
    report.update({
        'vertices': int(sum(len(m.positions) for m in meshes)),
        'triangles': int(sum(len(m.faces) for m in meshes)),
        'meshes': len(meshes), 'materials': len(materials),
        'textures': sorted(textures),
        'height': round(float(max(max(p[1] for p in m.positions) for m in meshes)), 3) if meshes else 0,
    })
    return Character(skel_bytes, body_bytes, textures, plan, report, [new_world[b.id] for b in tgt.bones])


def b_world(tgt: Target, i: int) -> np.ndarray:
    return tgt.bones[i].world


def _solid_tint(color) -> bytes:
    rgb = tuple(int(max(0.0, min(1.0, c)) * 255) for c in color[:3])
    return _solid_png(rgb)


def _split_faces(faces: np.ndarray, max_verts: int) -> list[np.ndarray]:
    if len(np.unique(faces)) <= max_verts:
        return [faces]
    out, cur, seen = [], [], set()
    for f in faces:
        new = set(f.tolist()) - seen
        if len(seen) + len(new) > max_verts:
            out.append(np.array(cur))
            cur, seen = [], set()
            new = set(f.tolist())
        cur.append(f)
        seen |= new
    if cur:
        out.append(np.array(cur))
    return out


def _declaration(data: bytes) -> str:
    m = re.match(rb'\s*(<\?xml[^>]*\?>)', data[3:] if data.startswith(b'\xef\xbb\xbf') else data)
    return m.group(1).decode('ascii', errors='replace') if m else '<?xml version="1.0" ?>'


def load_model_bytes(data: bytes) -> Model:
    import os
    import tempfile
    fd, p = tempfile.mkstemp(suffix='.nwmt')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        return load_model(p)
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def _update_helpers(hl: ET.Element, tgt: Target, new_world: dict, s_root: float, s_hand: dict) -> None:
    """Helpers linked to a bone keep their offset in that bone's frame (scaled with the body);
    ones linked to the scene root are moved with the body scale."""
    names = {b.name: b.id for b in tgt.bones}
    for h in hl:
        link = h.find('link')
        world = h.find('transform')
        if link is None or world is None:
            continue
        bid = link.get('bone_id')
        lt = link.find('transform')
        if bid is not None and int(bid) in new_world and lt is not None:
            i = int(bid)
            loc = _col(read_tm(lt))
            slot = tgt.bones[i].slot or ''
            side = slot.split('.')[1] if '.' in slot else None
            k = s_hand[side] if side and slot.startswith(('hand', 'thumb', 'fingers')) else s_root
            loc[:3, 3] *= k
            _set_tm(lt, _rows(loc))
            _set_tm(world, _rows(new_world[i] @ loc))
        else:
            name = link.get('name', '')
            if name in names:
                i = names[name]
                loc = np.linalg.inv(tgt.bones[i].world) @ _col(read_tm(world))
                loc[:3, 3] *= s_root
                _set_tm(world, _rows(new_world[i] @ loc))
            else:
                m = _col(read_tm(world))
                m[:3, 3] *= s_root
                _set_tm(world, _rows(m))


# =========================================================================== animations

_POS = re.compile(rb'(<pos\b[^>]*\bvalue\s*=\s*")([^"]*)(")')


def retarget_animation(data: bytes, plan: dict) -> bytes:
    """Rewrite the bone position keys of one .nwat; rotations, scales, mesh tracks, tags,
    sounds and effects stay byte-identical."""
    root_id = plan['root']
    s_root = plan['s_root']
    bones = plan['bones']
    out = []
    pos = 0
    # walk <bone_animation_list> ... </bone_animation_list> textually to keep the file's layout
    start = data.find(b'<bone_animation_list')
    if start < 0:
        return data
    end = data.find(b'</bone_animation_list>', start)
    if end < 0:
        return data
    out.append(data[:start])
    body = data[start:end]
    anim_re = re.compile(rb'<animation\b[^>]*\bid\s*=\s*"(\d+)"[^>]*>(.*?)</animation>', re.S)
    last = 0
    for m in anim_re.finditer(body):
        out.append(body[last:m.start(2)])
        bid = int(m.group(1))
        seg = m.group(2)
        pk = re.search(rb'<pos_key\b.*?</pos_key>', seg, re.S)
        if pk is not None and (bid == root_id or bid in bones):
            def fix(mm, bid=bid):
                vals = [float(v) for v in mm.group(2).split()]
                if len(vals) < 3:
                    return mm.group(0)
                v = np.array(vals[:3])
                if bid == root_id:
                    nv = v * s_root
                else:
                    lo, ln, k, scale_all = bones[bid]
                    nv = v * s_root if scale_all else ln + (v - lo) * k
                return mm.group(1) + fmt_vec(nv).encode() + mm.group(3)
            seg = seg[:pk.start()] + _POS.sub(fix, seg[pk.start():pk.end()]) + seg[pk.end():]
        out.append(seg)
        last = m.end(2)
    out.append(body[last:])
    out.append(data[end:])
    return b''.join(out)


# =========================================================================== hiding parts

def collapse_model(data: bytes) -> bytes:
    """Make a part invisible but keep what other code may refer to: materials (the face's
    eye/mouth textures are swapped by index), mesh ids and names, helpers.  Every mesh becomes
    one zero-area triangle on the root bone; hair/cloth physics (special bones) is dropped."""
    m = load_model_bytes(data)
    for mesh in m.meshes:
        if not mesh.positions:
            continue
        p = tuple(mesh.positions[0])
        mesh.positions = [p] * 3
        for attr in ('normals', 'tangents', 'uvs'):
            v = getattr(mesh, attr)
            if v:
                setattr(mesh, attr, [v[0]] * 3)
        if mesh.skin is not None:
            mesh.skin = [[(0, 1.0), (0, 0.0), (0, 0.0), (0, 0.0)]] * 3
            mesh.use_bone = 1
        mesh.faces = [(0, 1, 2)]
        mesh.bound_box = [p[0], p[1], p[2], p[0], p[1], p[2]]
        mesh.bbox_from_file = False
        mesh.index_attrs = {'b32bit': '0'}
    sections = []
    for tag, e in m.sections:
        if tag == 'physics_list':
            continue
        if tag == 'special_bone_list':
            e = ET.Element('special_bone_list', {'count': '0'})
            m.raw.pop(tag, None)
        sections.append((tag, e))
    m.sections = sections
    if m.header is not None:
        if m.header.get('special_bone') is not None:
            m.header.set('special_bone', '0')
        if m.header.get('physics') is not None:
            m.header.set('physics', '0')
    m.raw.pop('bound_box', None)
    return model_to_xml(m)


# =========================================================================== one-call API

def prepare_source(path: str, height: float, blender: str | None = None, turn: float = 0.0,
                   scene: Scene | None = None):
    """Load + detect + normalise a source model.  Returns (scene, rig, report)."""
    from . import scene3d
    sc = scene if scene is not None else scene3d.load(path, blender)
    rig = detect_rig(sc)
    rep = {'format': sc.fmt, 'source': sc.counts(), 'notes': list(sc.notes)}
    if rig is None:
        rep['notes'].append('사람 뼈대를 인식하지 못해 메시 전체를 골반에 붙입니다(팔다리가 움직이지 않음)')
        for m in sc.meshes:          # bone 0 of no rig -> 'hips' in _vertex_weights
            m.joints = np.zeros((len(m.positions), 1), dtype=np.int64)
            m.weights = np.ones((len(m.positions), 1))
    else:
        rep['mapping'] = {k: sc.bones[v].name for k, v in rig.slots.items() if v is not None}
        rep['spine'] = [sc.bones[b].name for b in rig.spine]
        rep['fingers'] = {s: {k: len(v) for k, v in f.items()} for s, f in rig.fingers.items()}
    rep.update(normalize_scene(sc, rig, height, turn))
    return sc, rig, rep
