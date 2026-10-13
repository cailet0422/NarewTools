"""Block atlas tiles and furniture/model texture lookups."""
from __future__ import annotations

from PIL import Image

from narew.modfmt import entry_key

from . import imaging, tables
from .core import Studio, StudioError


def layer_name(layer: str) -> str:
    if layer not in tables.ATLAS:
        raise StudioError('layer는 diffuse 또는 specular')
    return tables.ATLAS[layer]


def check_tile(i) -> int:
    i = int(i)
    if not 0 <= i < imaging.ATLAS_GRID ** 2:
        raise StudioError('타일 번호 범위: 0–255')
    return i


def bricks(st: Studio) -> list[dict]:
    return tables.bricks(st.read(tables.BRICK_TABLE))


def blocks_info(st: Studio) -> dict:
    bl = bricks(st)
    usage: dict[int, list[int]] = {}
    for b in bl:
        for t in set(b['tiles'].values()):
            if t >= 0:
                usage.setdefault(t, []).append(b['id'])
    changed = {}
    for layer, name in tables.ATLAS.items():
        changed[layer] = []
        if entry_key(name) in st.project_files():
            g = imaging.open_image(st.read(name, 'game')).convert('RGBA')
            p = imaging.open_image(st.read(name)).convert('RGBA')
            size = imaging.atlas_tile_size(g)
            for i in range(imaging.ATLAS_GRID ** 2):
                box = imaging.atlas_box(i, size)
                if g.crop(box).tobytes() != p.crop(box).tobytes():
                    changed[layer].append(i)
    return {'bricks': bl, 'usage': usage, 'changed': changed}


def atlas_image(st: Studio, layer: str = 'diffuse', source: str = 'auto') -> Image.Image:
    return imaging.open_image(st.read(layer_name(layer), source)).convert('RGBA')


def tile(st: Studio, i: int, layer: str = 'diffuse', source: str = 'auto') -> Image.Image:
    return imaging.get_tile(st.read(layer_name(layer), source), check_tile(i))


def write_tile(st: Studio, i: int, layer: str, tile_im: Image.Image):
    name = layer_name(layer)
    new = imaging.set_tile(st.read(name), check_tile(i), tile_im)
    game = st.read(name, 'game')
    if imaging.open_image(new).convert('RGBA').tobytes() == imaging.open_image(game).convert('RGBA').tobytes():
        st.revert(name)
    else:
        st.write(name, new)


def tile_from_path(st: Studio, i: int, layer: str, path: str):
    try:
        im = Image.open(path)
        im.load()
    except Exception as ex:
        raise StudioError(f'이미지를 읽을 수 없습니다: {ex}')
    write_tile(st, i, layer, im)


def tile_adjust(st: Studio, i: int, layer: str, params: dict, apply: bool = True) -> Image.Image:
    out = imaging.adjust(tile(st, i, layer), **params)
    if apply:
        write_tile(st, i, layer, out)
    return out


def tile_reset(st: Studio, i: int, layer: str):
    write_tile(st, i, layer, tile(st, i, layer, 'game'))


def free_tiles(st: Studio) -> list[int]:
    """Atlas cells no brick refers to and that are empty (fully transparent or magenta)."""
    used = set()
    for b in bricks(st):
        used.update(t for t in b['tiles'].values() if t >= 0)
    atlas = atlas_image(st)
    size = imaging.atlas_tile_size(atlas)
    out = []
    for i in range(imaging.ATLAS_GRID ** 2):
        if i in used:
            continue
        cell = atlas.crop(imaging.atlas_box(i, size)).resize((8, 8))
        px = list(cell.getdata())
        if all(a == 0 or (r > 240 and g < 20 and b > 240) for r, g, b, a in px):
            out.append(i)
    return out


# ---------------------------------------------------------------------------- products / models

def products(st: Studio) -> list[dict]:
    """Products with their model entry (engine rule: 'object\\' + <model>)."""
    out = []
    for p in tables.products(st.read(tables.PRODUCT_TABLE)):
        m = p['model']
        if not m or not m.lower().endswith('.nwmt'):
            continue
        entry = 'Object\\' + m.replace('/', '\\')
        p['entry'] = st.canonical_name(entry) if st.exists(entry) else None
        out.append(p)
    return out


def model_textures(st: Studio, model_entry: str) -> dict:
    name = st.canonical_name(model_entry)
    mats = tables.model_materials(st.read(name))
    seen = {}
    for m in mats:
        resolved = {}
        for tag, ref in m['textures'].items():
            want = tables.engine_texture_entry(name, ref)
            if want is None:
                resolved[tag] = {'ref': ref, 'entry': None, 'status': 'unused'}
                continue
            if st.exists(want):
                hit = st.canonical_name(want)
                resolved[tag] = {'ref': ref, 'entry': hit, 'status': st.status_of(hit), 'expected': want}
                seen[entry_key(hit)] = hit
            else:
                resolved[tag] = {'ref': ref, 'entry': None, 'status': 'missing', 'expected': want}
        m['textures'] = resolved
    return {'model': name, 'materials': mats, 'textures': sorted(seen.values())}
