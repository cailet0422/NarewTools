"""Reader/writer for Islet Online brick (voxel) data: ``.iaf`` and ``.bwr``.

Recovered from IsletEngine_release(x64).dll (see docs/05-brick-formats.md for
addresses).  All integers are little-endian.

A *brick* is one byte (``Islet::SBrick``): 0 = air, 1..254 = brick id from
``XML/brick_table.xml``, 255 = invisible collision filler.  Bricks are stored in
16 x H x 16 columns ("groups" / "regions") indexed ``(y * 16 + z) * 16 + x``
(``IBrickGroup::GetLocalBrick``, ``IBrickServerRegion::GetBrick``), y is up.

``.iaf`` (``CBrickArchitecture::Load/Save``) - a prefab building / tree::

    u32 magic = 0x203
    i32 width (x), height (y), depth (z)
    i32 group_count = (width / 16) * (depth / 16)
    group_count x { u32 size; zlib[size] -> height * 256 bricks }
    char trailer[128]          name buffer; the game zeroes the first 2 bytes

``.bwr`` (``IBrickServer::SaveTo`` / ``IWorldFile::Create``) - a whole world::

    u32 magic = 0x870203, i32 width (x), i32 depth (z), i32 region_count
    region_count x region      (16 x 256 x 16 bricks each, row-major by z then x)
    tail                       3 x i64, float start_pos[3], server state ...
    u32 region_offset[region_count]   absolute file offset of every region

    region (IBrickServerRegion::SaveToFile):
      i64 version; i32 size; zlib[size] -> 65536 bricks
      i32 n; u32 special[n]      (the server ignores the list when n >= 256)
      i32 n; object[n]
      i32 0, 0, 0

    object (IWorldObject::SaveBinary):
      u8 kind (1 tree, 2 product); i32 type_id; u16 x, y, z; u8 rot; u8 state;
      u32 age; i32 hp; i64 owner; extra (size depends on the product type)

The original tools compress ``.iaf`` with zlib level 6 and ``.bwr`` with level 1;
both settings reproduce every shipped stream byte for byte, and unchanged
streams are copied verbatim anyway.
"""
from __future__ import annotations

import math
import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Iterator

IAF_MAGIC = 0x203
BWR_MAGIC = 0x870203
BWR_MAGIC_OLD = 0x870202          # no width/depth in the header (accepted by the loader)
SIDE = 16                         # a group/region is SIDE x height x SIDE bricks
REGION_HEIGHT = 256
IAF_LEVEL = 6
BWR_LEVEL = 1
AIR = 0
COLLISION = 0xFF
MAX_SPECIALS = 256                # IBrickServerRegion::LoadFromFile skips longer lists

OBJ_TREE = 1
OBJ_PRODUCT = 2

_HDR = struct.Struct('<5i')
_OBJ = struct.Struct('<BiHHHBBIiq')        # IWorldObject::SaveBinary, 30 bytes
_ITEM = struct.Struct('<iHHBBBB')          # IItem::SaveBin up to the extra-type byte
_ITEM_TAIL = 4 * 4 + 2 + 4 * 2 + 8 * 2     # rest of IItem + CServerItem::SaveBin


class BrickError(Exception):
    pass


def brick_index(x: int, y: int, z: int) -> int:
    return (y * SIDE + z) * SIDE + x


def zlib_level(stream: bytes) -> int:
    """Compression level to use when re-encoding, from the zlib header's FLEVEL bits."""
    if len(stream) < 2:
        return IAF_LEVEL
    return (1, 5, 6, 9)[stream[1] >> 6]


class Column:
    """One 16 x height x 16 block of bricks, decompressed on first access."""
    __slots__ = ('height', 'level', '_comp', '_data')

    def __init__(self, height: int, comp: bytes | None = None,
                 data: bytes | bytearray | None = None, level: int = BWR_LEVEL):
        self.height = height
        self.level = zlib_level(comp) if comp else level
        self._comp = comp
        self._data = bytearray(data) if data is not None else None
        if comp is None and data is None:
            self._data = bytearray(height * SIDE * SIDE)
        if self._data is not None and len(self._data) != height * SIDE * SIDE:
            raise BrickError(f'column data is {len(self._data)} bytes, '
                             f'expected {height * SIDE * SIDE}')

    @property
    def data(self) -> bytearray:
        if self._data is None:
            raw = zlib.decompress(self._comp)
            if len(raw) != self.height * SIDE * SIDE:
                raise BrickError(f'brick stream inflates to {len(raw)} bytes, '
                                 f'expected {self.height * SIDE * SIDE}')
            self._data = bytearray(raw)
        return self._data

    def get(self, x: int, y: int, z: int) -> int:
        return self.data[brick_index(x, y, z)]

    def peek(self) -> bytes:
        """The bricks without keeping a decompressed copy (one-off reads across a big world)."""
        if self._data is not None:
            return bytes(self._data)
        return zlib.decompress(self._comp)

    def set(self, x: int, y: int, z: int, value: int):
        self.data[brick_index(x, y, z)] = value
        self._comp = None

    def touch(self):
        """Call after editing ``data`` in place so the stream is recompressed."""
        _ = self.data
        self._comp = None

    def compressed(self) -> bytes:
        if self._comp is None:
            self._comp = zlib.compress(bytes(self._data), self.level)
        return self._comp

    def is_empty(self) -> bool:
        return not any(self.data)


# --------------------------------------------------------------------------- .iaf

@dataclass
class Architecture:
    """``.iaf`` prefab (Islet::CBrickArchitecture)."""
    width: int
    height: int
    depth: int
    groups: list[Column] = field(default_factory=list)
    trailer: bytes = b'\0' * 128

    @classmethod
    def new(cls, width: int, height: int, depth: int) -> 'Architecture':
        if width % SIDE or depth % SIDE or not 0 < height <= 0xFFFF:
            raise BrickError('width and depth must be multiples of 16')
        n = (width // SIDE) * (depth // SIDE)
        return cls(width, height, depth,
                   [Column(height, level=IAF_LEVEL) for _ in range(n)])

    @classmethod
    def from_bytes(cls, d: bytes) -> 'Architecture':
        if len(d) < _HDR.size:
            raise BrickError('file too small for an .iaf header')
        magic, w, h, dp, n = _HDR.unpack_from(d, 0)
        if magic != IAF_MAGIC:
            raise BrickError(f'bad .iaf magic {magic:#x}')
        if w <= 0 or h <= 0 or dp <= 0 or n != (w // SIDE) * (dp // SIDE):
            raise BrickError(f'inconsistent .iaf header {w}x{h}x{dp}, {n} groups')
        p = _HDR.size
        groups = []
        for _ in range(n):
            size, = struct.unpack_from('<I', d, p)
            p += 4
            if p + size > len(d):
                raise BrickError('group stream runs past end of file')
            groups.append(Column(h, comp=bytes(d[p:p + size]), level=IAF_LEVEL))
            p += size
        trailer = bytes(d[p:])
        if len(trailer) != 128:
            raise BrickError(f'expected a 128-byte trailer, found {len(trailer)} bytes')
        return cls(w, h, dp, groups, trailer)

    @classmethod
    def read(cls, path: str) -> 'Architecture':
        with open(path, 'rb') as f:
            return cls.from_bytes(f.read())

    def to_bytes(self) -> bytes:
        out = [_HDR.pack(IAF_MAGIC, self.width, self.height, self.depth, len(self.groups))]
        for g in self.groups:
            c = g.compressed()
            out += [struct.pack('<I', len(c)), c]
        out.append(self.trailer.ljust(128, b'\0')[:128])
        return b''.join(out)

    def write(self, path: str):
        _write_atomic(path, self.to_bytes())

    @property
    def size(self) -> tuple[int, int, int]:
        return self.width, self.height, self.depth

    def group(self, gx: int, gz: int) -> Column:
        return self.groups[gz * (self.width // SIDE) + gx]

    def get(self, x: int, y: int, z: int) -> int:
        return self.group(x // SIDE, z // SIDE).get(x % SIDE, y, z % SIDE)

    def set(self, x: int, y: int, z: int, value: int):
        self.group(x // SIDE, z // SIDE).set(x % SIDE, y, z % SIDE, value)

    def iter_bricks(self) -> Iterator[tuple[int, int, int, int]]:
        """Yield (x, y, z, brick) for every non-air brick."""
        gw = self.width // SIDE
        for i, g in enumerate(self.groups):
            yield from _iter_column(g, (i % gw) * SIDE, (i // gw) * SIDE)


def _iter_column(col: Column, ox: int, oz: int):
    data = col.data
    for i in range(len(data)):
        v = data[i]
        if v:
            y, r = divmod(i, SIDE * SIDE)
            z, x = divmod(r, SIDE)
            yield ox + x, y, oz + z, v


# ------------------------------------------------------------------ product table

# PRODUCT_TYPE names, index = enum value (pointer table at 0x180398d90)
PRODUCT_EVENTS = (
    'none', 'on', 'sitdown', 'sleep', 'door', 'board', 'storage', 'cook', 'customize',
    'experiment', 'smelting', 'sawmilling', 'tanning', 'tool', 'furniture', 'machinery',
    'compounding', 'clothes', 'etc', 'plant ecology', 'dyeing', 'bomb', 'feed', 'food',
    'photo', 'movie', 'target_board', 'treasure', 'store', 'water_tank', 'kitchen_garden',
    'disassembly', 'cloth_dummy', 'item_holder', 'game_machine', 'fish_tank', 'flower_pot',
    'decor_obj', 'archeology', 'auto_craft', 'altar', 'mailbox', 'windmill', 'instrument',
    'painter', 'rides', 'name_tag', 'waste_box', 'skin_customize', 'paper_making',
    'password_object', 'horror', 'generator', 'logic_gate', 'sports_day', 'score', 'timer',
    'balloon', 'roulette', 'chac', 'event_make', 'electronics', 'store_find', 'wall_clock',
    'vehicle', 'boardgame', 'rank', 'decor_obj_mask', 'tracking_target', 'market', 'trap',
    'post')
_EVENT_ID = {n: i for i, n in enumerate(PRODUCT_EVENTS)}

# IProductType::SeekExtraBin (0x18018ca80) groups
_SIZED = {5, 0x18, 0x19, 0x1e, 0x27, 0x2a}                        # u16 n + n bytes
_CONTAINER = {6, 0x16, 0x1d, 0x20, 0x21, 0x23, 0x24, 0x25, 0x29, 0x34, 0x35}
_STORE = 0x1c
_MAILBOX = 0x29
OPT_COLOR = 0x2                                                    # <level option> bit


@dataclass
class ProductType:
    id: int
    name: str
    event: int
    option: int
    level: int = 0                      # <level value>; for containers = number of slots
    hp: int = 1000                      # <stat hp> (placed objects carry it, observed 809/809)
    bmin: tuple[float, float, float] | None = None     # <min> / <max> model box around the anchor
    bmax: tuple[float, float, float] | None = None


class ProductTable:
    """Minimal view of XML/product.xml: what the loader needs to size object data."""

    def __init__(self, types: dict[int, ProductType] | None = None):
        self.types = types or {}

    @classmethod
    def read(cls, path: str) -> 'ProductTable':
        with open(path, encoding='utf-8') as f:
            return cls.parse(f.read())

    @classmethod
    def parse(cls, text: str) -> 'ProductTable':
        types = {}
        for pid, body in re.findall(r'<product id="(\d+)"[^>]*>(.*?)</product>', text, re.S):
            ev = re.search(r'<event>(.*?)</event>', body)
            opt = re.search(r'<level\b[^>]*\boption="(\d+)"', body)
            lvl = re.search(r'<level\b[^>]*\bvalue="(\d+)"', body)
            hp = re.search(r'<stat\b[^>]*\bhp="(\d+)"', body)
            name = re.search(r'<name>(.*?)</name>', body)
            box = [re.search(rf'<{t}\b[^>]*\bx="([-\d.eE]+)"[^>]*\by="([-\d.eE]+)"[^>]*\bz="([-\d.eE]+)"', body)
                   for t in ('min', 'max')]
            event = _EVENT_ID.get(ev.group(1).strip().lower(), 0) if ev else 0
            types[int(pid)] = ProductType(
                int(pid), name.group(1) if name else '', event, int(opt.group(1)) if opt else 0,
                int(lvl.group(1)) if lvl else 0, int(hp.group(1)) if hp else 1000,
                *(tuple(float(v) for v in m.groups()) if m else None for m in box))
        return cls(types)

    def get(self, pid: int) -> ProductType | None:
        return self.types.get(pid)


# --------------------------------------------------------------------------- placing products

def _axis_cells(lo: float, hi: float) -> list[int]:
    lo, hi = min(lo, hi), max(lo, hi)
    r = list(range(math.ceil(lo - 1e-6), math.floor(hi + 1e-6) + 1))
    return r or [0]


def product_footprint(pt: ProductType, rot: int) -> list[tuple[int, int, int]]:
    """Cells (dx, dy, dz) relative to the anchor that a placed product fills with collision
    bricks (255).  Rule (matches all 76 isolated products in the shipped maps + a game save):
    the anchor is the centre of its cell; the <min>/<max> box is turned by rot x 90 degrees
    ((x, z) -> rot1 (z, -x), rot2 (-x, -z), rot3 (-z, x)); a cell counts when its centre lies in
    the box (x/z: d in [min, max]; y: dy + 0.5 in [min_y, max_y]); an empty axis means the anchor."""
    if pt.bmin is None or pt.bmax is None:
        return [(0, 0, 0)]
    (x0, y0, z0), (x1, y1, z1) = pt.bmin, pt.bmax
    rot &= 3
    if rot == 1:
        x0, z0, x1, z1 = z0, -x1, z1, -x0
    elif rot == 2:
        x0, z0, x1, z1 = -x1, -z1, -x0, -z0
    elif rot == 3:
        x0, z0, x1, z1 = -z1, x0, -z0, x1
    ys = [y for y in _axis_cells(y0 - 0.5, y1 - 0.5) if y >= 0] or [0]
    return [(dx, dy, dz) for dy in ys for dz in _axis_cells(z0, z1) for dx in _axis_cells(x0, x1)]


def placeable(pt: ProductType) -> str | None:
    """None when a new object of this product can be written, else the reason it cannot."""
    if pt.event == _STORE:
        return '상점은 진열 정보가 필요해 아직 놓을 수 없습니다'
    if pt.event == _MAILBOX:
        return '우편함은 편지 정보가 필요해 아직 놓을 수 없습니다'
    if pt.event in _SIZED:
        return f'"{PRODUCT_EVENTS[pt.event]}" 종류는 자체 데이터가 필요해 아직 놓을 수 없습니다'
    return None


def new_product_extra(pt: ProductType) -> bytes:
    """Extra data of a freshly placed product, in the form IProductType::SeekExtraBin expects:
    containers: u8 slots (= <level value>), u8 0 (no colours), slots x u8 0 (empty);
    others: [12 zero colour bytes if <level option> & 2] + u16 0 (as every shipped object)."""
    why = placeable(pt)
    if why:
        raise BrickError(why)
    if pt.event in _CONTAINER:
        n = max(0, min(255, pt.level))
        return bytes([n, 0]) + bytes(n)
    return (bytes(12) if pt.option & OPT_COLOR else b'') + b'\x00\x00'


def new_product(pt: ProductType, x: int, y: int, z: int, rot: int = 0, owner: int = 0) -> 'WorldObject':
    return WorldObject(OBJ_PRODUCT, pt.id, x, y, z, rot & 3, 0, 0, pt.hp, owner, new_product_extra(pt))


class _Cursor:
    __slots__ = ('d', 'p', 'end')

    def __init__(self, d: bytes, p: int, end: int):
        self.d, self.p, self.end = d, p, end

    def take(self, n: int) -> bytes:
        if n < 0 or self.p + n > self.end:
            raise BrickError('record runs past end of region')
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack('<H', self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack('<i', self.take(4))[0]


def _skip_item(c: _Cursor):
    """IItem::LoadBin + CServerItem::LoadBin."""
    c.take(_ITEM.size - 1)
    extra = c.u8()
    if extra:
        raise BrickError(f'item extra data type {extra} is not decoded')
    c.take(_ITEM_TAIL)


def _skip_extra(c: _Cursor, kind: int, type_id: int, products: ProductTable | None):
    """Advance over an object's extra data the way IProductType::SeekExtraBin does."""
    if kind == OBJ_TREE:                       # ITreeObject: u16 version, then version bytes
        c.take(c.u16())
        return
    if kind != OBJ_PRODUCT:
        raise BrickError(f'unknown world object kind {kind}')
    pt = products.get(type_id) if products else None
    if pt is None:
        raise BrickError(f'product {type_id} not in product table')
    ev = pt.event
    if ev in _SIZED:
        c.take(c.u16())
    elif ev in _CONTAINER:
        n = c.u8()
        if c.u8():
            c.take(12)                          # three ARGB colours
        for _ in range(n):
            if c.u8():
                _skip_item(c)
        if ev == _MAILBOX:
            c.take(0x48 + n * 72)
    elif ev == _STORE:
        c.take(0x62)
        for _ in range(c.u16()):
            if c.u8():
                _skip_item(c)
                c.take(9 + 0x38)
    else:
        if pt.option & OPT_COLOR:
            c.take(12)
        c.take(c.u16())


# --------------------------------------------------------------------------- .bwr

@dataclass
class WorldObject:
    """A tree or placed product (IWorldObject::SaveBinary)."""
    kind: int
    type_id: int
    x: int
    y: int
    z: int
    rot: int = 0
    state: int = 0
    age: int = 0
    hp: int = 1000
    owner: int = 0
    extra: bytes = b''

    def to_bytes(self) -> bytes:
        return _OBJ.pack(self.kind, self.type_id, self.x, self.y, self.z, self.rot,
                         self.state, self.age, self.hp, self.owner) + self.extra


@dataclass
class Region:
    """16 x 256 x 16 bricks plus the objects anchored in them."""
    rx: int
    rz: int
    version: int = 1
    column: Column = field(default_factory=lambda: Column(REGION_HEIGHT))
    specials: list[int] = field(default_factory=list)
    objects: list[WorldObject] | None = field(default_factory=list)
    objects_raw: bytes | None = None   # object block kept verbatim when it can't be split
    trailer: tuple[int, int, int] = (0, 0, 0)

    def get(self, x: int, y: int, z: int) -> int:
        return self.column.get(x, y, z)

    def set(self, x: int, y: int, z: int, value: int):
        self.column.set(x, y, z, value)

    def to_bytes(self) -> bytes:
        comp = self.column.compressed()
        out = [struct.pack('<qi', self.version, len(comp)), comp,
               struct.pack('<i', len(self.specials)),
               struct.pack(f'<{len(self.specials)}I', *self.specials)]
        if self.objects is not None:
            out.append(struct.pack('<i', len(self.objects)))
            out += [o.to_bytes() for o in self.objects]
        else:
            out.append(self.objects_raw)
        out.append(struct.pack('<3i', *self.trailer))
        return b''.join(out)


@dataclass
class WorldTail:
    """Everything between the last region and the offset table, kept verbatim.

    Two layouts occur in shipped files:

    * ``mapfile`` - what ``IWorldFile::Create`` (0x1800606d0) reads:
      ``i64 a, i64 b, i64 c, float start_pos[3]`` then the sky record.
    * ``server`` - ``IBrickServer::SaveTail`` (0x180048920): i64 object serial,
      balls, game-mode state, fishing contest, contests, ``u8 1, i32 seed,
      u8[3], i64 save_time, float start_pos[3]`` then the sky record.

    Both continue with the CSkyTimer record (``u8 0x22, i32, i32, u32 seconds``),
    the ``0x771231`` list (i32 n, n x 44 bytes), 16 bytes, land ownership and
    the ``0xfc8818c8`` block.  ``view()`` locates the known fields by the
    ``0x771231`` marker; ``set_start_pos`` patches the spawn point in place.
    """
    raw: bytes = b''

    MARKER = struct.pack('<I', 0x771231)

    def to_bytes(self) -> bytes:
        return self.raw

    def _anchor(self) -> int | None:
        m = self.raw.find(self.MARKER)
        if m >= 33 and self.raw[m - 13] == 0x22:
            return m
        return None

    @property
    def layout(self) -> str:
        m = self._anchor()
        if m is None:
            return 'unknown'
        return 'mapfile' if m == 49 else 'server'

    def view(self) -> dict:
        m = self._anchor()
        if m is None:
            return {'layout': 'unknown', 'size': len(self.raw)}
        r = self.raw
        sky = struct.unpack_from('<iiI', r, m - 12)
        out = {'layout': self.layout, 'size': len(r),
               'start_pos': list(struct.unpack_from('<3f', r, m - 25)),
               'sky': {'version': 0x22, 'value_1c': sky[0], 'value_18': sky[1],
                       'time_of_day_s': sky[2]}}
        n, = struct.unpack_from('<i', r, m + 4)
        out['list_771231_count'] = n
        if out['layout'] == 'mapfile':
            out['counters'] = list(struct.unpack_from('<3q', r, 0))
        else:
            out['object_serial'] = struct.unpack_from('<q', r, 0)[0]
            out['save_time'] = struct.unpack_from('<q', r, m - 33)[0]
            if r[m - 42] == 1:
                out['seed'] = struct.unpack_from('<i', r, m - 41)[0]
        return out

    @property
    def start_pos(self) -> tuple[float, float, float] | None:
        m = self._anchor()
        return struct.unpack_from('<3f', self.raw, m - 25) if m is not None else None

    def set_start_pos(self, x: float, y: float, z: float):
        m = self._anchor()
        if m is None:
            raise BrickError('cannot locate the start position in this tail')
        b = bytearray(self.raw)
        struct.pack_into('<3f', b, m - 25, x, y, z)
        self.raw = bytes(b)


@dataclass
class World:
    """``.bwr`` world (Islet::IBrickServer / IWorldFile)."""
    width: int
    depth: int
    regions: list[Region] = field(default_factory=list)
    tail: WorldTail = field(default_factory=WorldTail)
    magic: int = BWR_MAGIC

    @property
    def regions_x(self) -> int:
        return self.width // SIDE

    @property
    def regions_z(self) -> int:
        return self.depth // SIDE

    @classmethod
    def from_bytes(cls, d: bytes, products: ProductTable | None = None,
                   strict: bool = False) -> 'World':
        magic, = struct.unpack_from('<I', d, 0)
        if magic == BWR_MAGIC:
            width, depth, count = struct.unpack_from('<3i', d, 4)
            p = 16
        elif magic == BWR_MAGIC_OLD:
            raise BrickError('0x870202 worlds (no size in header) are not supported')
        else:
            raise BrickError(f'bad .bwr magic {magic:#x}')
        if width % SIDE or depth % SIDE or count != (width // SIDE) * (depth // SIDE):
            raise BrickError(f'inconsistent .bwr header {width}x{depth}, {count} regions')
        table_at = len(d) - 4 * count
        offsets = struct.unpack_from(f'<{count}I', d, table_at)
        w = cls(width, depth, magic=magic)
        rx = width // SIDE
        for i in range(count):
            if offsets[i] != p:
                raise BrickError(f'region {i} at {p}, offset table says {offsets[i]}')
            end = offsets[i + 1] if i + 1 < count else None
            region, p = _read_region(d, p, end if end is not None else table_at,
                                     i % rx, i // rx, products, strict, last=end is None)
            w.regions.append(region)
        w.tail = WorldTail(bytes(d[p:table_at]))
        return w

    @classmethod
    def read(cls, path: str, products: ProductTable | None = None,
             strict: bool = False) -> 'World':
        with open(path, 'rb') as f:
            return cls.from_bytes(f.read(), products, strict)

    def to_bytes(self) -> bytes:
        out = [struct.pack('<I3i', self.magic, self.width, self.depth, len(self.regions))]
        pos = 16
        offsets = []
        for r in self.regions:
            b = r.to_bytes()
            offsets.append(pos)
            out.append(b)
            pos += len(b)
        out.append(self.tail.to_bytes())
        out.append(struct.pack(f'<{len(offsets)}I', *offsets))
        return b''.join(out)

    def write(self, path: str):
        _write_atomic(path, self.to_bytes())

    def region(self, rx: int, rz: int) -> Region:
        return self.regions[rz * self.regions_x + rx]

    def get(self, x: int, y: int, z: int) -> int:
        return self.region(x // SIDE, z // SIDE).get(x % SIDE, y, z % SIDE)

    def set(self, x: int, y: int, z: int, value: int):
        self.region(x // SIDE, z // SIDE).set(x % SIDE, y, z % SIDE, value)

    def iter_objects(self) -> Iterator[tuple[Region, WorldObject]]:
        for r in self.regions:
            for o in r.objects or ():
                yield r, o


def _read_region(d: bytes, p: int, end: int, rx: int, rz: int,
                 products: ProductTable | None, strict: bool, last: bool):
    version, size = struct.unpack_from('<qi', d, p)
    p += 12
    if size < 0 or p + size > end:
        raise BrickError(f'region ({rx},{rz}) brick stream runs past its end')
    col = Column(REGION_HEIGHT, comp=bytes(d[p:p + size]))
    p += size
    n, = struct.unpack_from('<i', d, p)
    p += 4
    if n < 0 or p + 4 * n > end:
        raise BrickError(f'region ({rx},{rz}) special list runs past its end')
    specials = list(struct.unpack_from(f'<{n}I', d, p))
    p += 4 * n
    obj_start = p
    r = Region(rx, rz, version, col, specials)
    try:
        c = _Cursor(d, p, end)
        objs = []
        for _ in range(c.i32()):
            head = _OBJ.unpack(c.take(_OBJ.size))
            start = c.p
            _skip_extra(c, head[0], head[1], products)
            objs.append(WorldObject(*head, extra=bytes(d[start:c.p])))
        r.objects = objs
        p = c.p
        r.trailer = struct.unpack_from('<3i', d, p)
        p += 12
        if not last and p != end:
            raise BrickError(f'{end - p} unexplained bytes at end of region')
    except (BrickError, struct.error) as ex:
        if strict or last:
            raise BrickError(f'region ({rx},{rz}): {ex}') from None
        # keep everything between the special list and the next region verbatim
        r.objects = None
        r.objects_raw = bytes(d[obj_start:end - 12])
        r.trailer = struct.unpack_from('<3i', d, end - 12)
        p = end
    return r, p


# ------------------------------------------------------------------------ helpers

def _write_atomic(path: str, data: bytes):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def find_xml_dir(*paths: str) -> str | None:
    """Locate an extracted ``XML`` folder (with product.xml) near the given files."""
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        while True:
            for cand in (os.path.join(d, 'xml', 'XML'), os.path.join(d, 'XML'),
                         os.path.join(d, 'xml')):
                if os.path.isfile(os.path.join(cand, 'product.xml')):
                    return cand
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def load_any(path: str, products: ProductTable | None = None):
    with open(path, 'rb') as f:
        d = f.read()
    magic, = struct.unpack_from('<I', d, 0)
    if magic == IAF_MAGIC:
        return Architecture.from_bytes(d)
    return World.from_bytes(d, products)
