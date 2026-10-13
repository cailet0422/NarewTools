"""Game data tables used by the editors (brick types, products), read from project-or-game."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from narew import nwxml

from .core import StudioError

BRICK_TABLE = 'XML\\brick_table.xml'
PRODUCT_TABLE = 'XML\\product.xml'
ATLAS = {'diffuse': 'terrain.png', 'specular': 'terrain_specular.png'}
FACES = ('front', 'back', 'left', 'right', 'top', 'bottom')
TEXTURE_TAGS = ('diffuse_map', 'normal_map', 'specular_map', 'mask_map')


def _parse(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as ex:
        raise StudioError(f'표 XML을 읽을 수 없습니다: {ex}')


def _text(e: ET.Element, tag: str) -> str:
    c = e.find(tag)
    return (c.text or '').strip() if c is not None else ''


def bricks(data: bytes) -> list[dict]:
    out = []
    for b in _parse(data).iter('brick'):
        try:
            bid = int(b.get('id'))
        except (TypeError, ValueError):
            continue  # header/template row
        g = b.find('geometry')
        geo = g.attrib if g is not None else {}
        tiles = {}
        for f in FACES:
            try:
                tiles[f] = int(geo.get(f'tile_{f}', '-1'))
            except ValueError:
                tiles[f] = -1
        try:
            height = int(geo.get('height', '10'))
        except ValueError:
            height = 10
        color = geo.get('color', '0xFFFFFFFF')
        out.append({'id': bid, 'name': _text(b, 'name'), 'label': _text(b, 'e_name'),
                    'category': _text(b, 'category'), 'height': height, 'color': color,
                    'alpha': geo.get('alpha', '0'), 'next': geo.get('next', '0'), 'tiles': tiles})
    return out


def products(data: bytes) -> list[dict]:
    out = []
    for p in _parse(data).iter('product'):
        try:
            pid = int(p.get('id'))
        except (TypeError, ValueError):
            continue
        out.append({'id': pid, 'name': _text(p, 'name'), 'event': _text(p, 'event'),
                    'model': _text(p, 'model'), 'animation': _text(p, 'animation')})
    return out


def model_materials(data: bytes) -> list[dict]:
    """Materials and their texture references from a .nwmt file."""
    root = nwxml.parse_bytes(data)
    mats = []
    for m in root.iter('material'):
        tex = {}
        for tag in TEXTURE_TAGS:
            c = m.find(tag)
            if c is not None and (c.text or '').strip():
                tex[tag] = c.text.strip()
        mats.append({'id': m.get('id'), 'name': m.get('name') or '', 'textures': tex})
    return mats


def engine_texture_entry(model_entry: str, ref: str) -> str | None:
    """Entry name the engine loads for a material texture (IMaterial::LoadTexture @0x180163750):
    empty or ':'-prefixed (artist absolute) paths load nothing; otherwise model folder + ref."""
    ref = (ref or '').strip()
    if not ref or ref.startswith(':'):
        return None
    parts = [p for p in (entry_dir(model_entry) + '\\' + ref.replace('/', '\\')).split('\\') if p and p != '.']
    out = []
    for p in parts:
        if p == '..':
            if out:
                out.pop()
        else:
            out.append(p)
    return '\\'.join(out)


def texture_basename(ref: str) -> str:
    """Exporter paths are often absolute artist paths; the engine uses the file name."""
    return ref.replace('/', '\\').rsplit('\\', 1)[-1].strip()


class BasenameIndex:
    """Find pack entries by bare file name (models/textures are referenced that way)."""

    def __init__(self, names):
        self.by_base: dict[str, list[str]] = {}
        for n in names:
            self.by_base.setdefault(texture_basename(n).lower(), []).append(n)

    def find(self, ref: str, prefer_dir: str | None = None, prefer_top: str | None = None) -> str | None:
        cands = self.by_base.get(texture_basename(ref).lower())
        if not cands:
            return None
        if prefer_dir:
            pd = prefer_dir.lower().rstrip('\\') + '\\'
            for c in cands:
                if c.lower().startswith(pd) and '\\' not in c[len(pd):]:
                    return c
        if prefer_top:
            for c in cands:
                if c.lower().startswith(prefer_top.lower() + '\\'):
                    return c
        return cands[0]


def entry_dir(name: str) -> str:
    return os.path.dirname(name.replace('/', '\\'))
