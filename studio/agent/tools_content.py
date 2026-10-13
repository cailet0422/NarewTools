"""Catalogs (bricks, furniture, items) and new content: blocks, furniture, items, sounds, block tiles."""
from __future__ import annotations

import os

from narew import brick

from .. import svc_blocks as B, tables
from ..core import StudioError
from ..creator import CRAFT_STATIONS, Names, Recipe
from . import render
from .registry import ARR, BOOL, INT, NUM, OBJ, Result, STR, tool

NAME_ARGS = dict(
    name_kor=STR('Korean name (shown in game).'),
    name_eng=STR('English name (default: Korean).', ''),
    desc_kor=STR('Korean description.', ''),
    desc_eng=STR('English description.', ''),
)
RECIPE = OBJ('Optional crafting recipe: {"station": "hand"|"furniture"|"tool"|"sewing"|"smelting"|"sawmilling"|'
             '"cooking"|"machinery"|"compounding"|"electronics" (or index), "ingredients": [[item_id, count], ...] '
             '(1-6, find ids with items_search), "count": 1, "level": 1, "exp": 10, "cooltime": 300}.', None)


def _names(name_kor, name_eng, desc_kor, desc_eng) -> Names:
    return Names(name_kor, name_eng, desc_kor, desc_eng)


def _recipe(d: dict | None) -> Recipe | None:
    if not d:
        return None
    st = d.get('station', 'hand')
    if isinstance(st, int):
        idx = st
    else:
        key = str(st).lower()
        keys = [('hand' if s[2] == 'none' else s[1].lower()) for s in CRAFT_STATIONS]
        if key not in keys:
            raise StudioError(f'모르는 작업대 {st!r}. 쓸 수 있는 것: {keys}')
        idx = keys.index(key)
    if not 0 <= idx < len(CRAFT_STATIONS):
        raise StudioError('작업대 번호가 범위 밖입니다')
    ings = [(int(a), int(b)) for a, b in d.get('ingredients', [])]
    if not 1 <= len(ings) <= 6:
        raise StudioError('재료는 1~6개입니다: [[아이템 번호, 개수], ...]')
    return Recipe(idx, ings, int(d.get('count', 1)), int(d.get('level', 1)), int(d.get('exp', 10)),
                  int(d.get('cooltime', 300)))


def _file(path: str | None, what: str) -> str | None:
    if path and not os.path.isfile(path):
        raise StudioError(f'{what} 파일이 없습니다: {path}')
    return path


# --------------------------------------------------------------------------- catalogs

@tool('bricks_list', 'content',
      'Brick (block) types for building: id, Korean/English name, category (rock, wood, glass, ...), height '
      '(10 = full block, less = slab), see-through. Use the ids with the world_* tools.',
      query=STR('Text in the name/category or an id.', None), category=STR('Only this category.', None),
      limit=INT('Max rows.', 300))
def bricks_list(ctx, query, category, limit):
    q = (query or '').strip().lower()
    out = []
    for b in B.bricks(ctx.st):
        text = f"{b['id']} {b['label']} {b['name']} {b['category']}".lower()
        if q and q not in text and q != str(b['id']):
            continue
        if category and b['category'].lower() != category.lower():
            continue
        out.append({'id': b['id'], 'name': b['label'] or b['name'], 'eng': b['name'], 'category': b['category'],
                    'height': b['height'], 'transparent': str(b.get('alpha', '0')) != '0'
                    or b['category'].lower() in ('glass', 'water', 'leaf', 'plant', 'snow', 'ice'),
                    'status': 'mod' if ctx.st.project and b['id'] in _mod_ids(ctx, tables.BRICK_TABLE, 'brick') else ''})
    return {'count': len(out), 'bricks': out[:limit],
            'special': {'0': 'air', '255': 'collision filler under furniture (do not paint with it)'}}


def _mod_ids(ctx, table, tag) -> set[int]:
    def make():
        from narew import xmlmerge
        p = ctx.st.read_patch(table)
        out = set()
        for r in xmlmerge.parse_patch(p) if p else []:
            if r.tag == tag and (r.get('id') or '').isdigit():
                out.add(int(r.get('id')))
        return out
    return make()


@tool('products_list', 'content',
      'Furniture / placeable object types (product.xml): id, name, kind (sleep, sit, light, container ...), '
      'footprint size in bricks (x, y, z), and why it cannot be placed (null = placeable with world_place).',
      query=STR('Text in the name/kind or an id.', None),
      placeable_only=BOOL('Only types world_place can place.', False), limit=INT('Max rows.', 100))
def products_list(ctx, query, placeable_only, limit):
    q = (query or '').strip().lower()
    table = ctx.world.product_table()
    models = {p['id']: p['entry'] or 'Object\\' + p['model'] for p in B.products(ctx.st)}
    out = []
    for pid, pt in sorted(table.types.items()):
        if pid == 0:
            continue
        ev = brick.PRODUCT_EVENTS[pt.event] if pt.event < len(brick.PRODUCT_EVENTS) else str(pt.event)
        why = brick.placeable(pt)
        if placeable_only and why:
            continue
        if q and q not in f'{pid} {pt.name} {ev}'.lower() and q != str(pid):
            continue
        cells = brick.product_footprint(pt, 0)
        size = [max(c[i] for c in cells) - min(c[i] for c in cells) + 1 for i in (0, 1, 2)]
        out.append({'id': pid, 'name': pt.name, 'kind': ev, 'size_xyz': size, 'not_placeable': why,
                    'model': models.get(pid)})
    return {'count': len(out), 'products': out[:limit]}


@tool('items_search', 'content', 'Items (for recipe ingredients / templates): id, Korean name, type.',
      query=STR('Text in the name or an id.', ''), type=STR('Only this item type value (e.g. "Product").', None),
      limit=INT('Max rows.', 50))
def items_search(ctx, query, type, limit):
    q = query.strip().lower()
    out = [i for i in ctx.creator.items(type)
           if not q or q in f"{i.get('id')} {i.get('kor', '')} {i.get('eng', '')} {i.get('name', '')}".lower()]
    return {'count': len(out), 'items': out[:limit]}


@tool('content_free_ids', 'content',
      'Unused ids for new content (engine limits: brick 1-254, product 1-1023, item 1-19999, craft 1-2599).',
      kind=STR('Which.', enum=('brick', 'product', 'item', 'craft')), limit=INT('How many to list.', 20))
def content_free_ids(ctx, kind, limit):
    c = ctx.creator
    ids = {'brick': c.free_brick_ids, 'product': c.free_product_ids, 'item': c.free_item_ids,
           'craft': c.free_craft_ids}[kind]()
    return {'kind': kind, 'free': len(ids), 'ids': ids[:limit]}


@tool('content_slots', 'content',
      'Where sounds/recipes can hook in: weather/time BGM slots (sky.xml), UI sound types (Gui\\sound.xml), '
      'crafting stations.')
def content_slots(ctx):
    c = ctx.creator
    return {'sky_bgm': c.sky_slots(), 'ui_sounds': c.ui_sound_slots(),
            'craft_stations': [{'station': 'hand' if s[2] == 'none' else s[1].lower(), 'label': s[0]}
                               for s in CRAFT_STATIONS]}


# --------------------------------------------------------------------------- new content

@tool('content_new_block', 'content',
      'Add a new block type: texture(s) go into free atlas tiles, a brick_table record copied from a template '
      'brick (hardness, tool, category), its item, name strings, an auto cube icon, optional recipe. '
      'Then build with it using its id.', writes=True,
      **NAME_ARGS,
      textures=OBJ('Image files: {"all": path} or any of {"top", "side", "bottom"} (make one with texture_make).'),
      template_brick=INT('Existing brick id to copy behaviour from (see bricks_list).', 1),
      brick_id=INT('New brick id (default: first free).', None),
      specular=STR('Optional specular (shine) image.', None),
      icon=STR('Optional icon image (default: rendered cube).', None),
      recipe=RECIPE)
def content_new_block(ctx, name_kor, name_eng, desc_kor, desc_eng, textures, template_brick, brick_id, specular,
                      icon, recipe):
    c = ctx.creator
    bad = [k for k in textures if k not in ('all', 'top', 'side', 'bottom')]
    if bad:
        raise StudioError(f'textures 키는 all/top/side/bottom만: {bad}')
    for k, v in textures.items():
        _file(v, f'텍스처({k})')
    if brick_id is None:
        free = c.free_brick_ids()
        if not free:
            raise StudioError('남은 블록 번호가 없습니다')
        brick_id = free[0]
    r = c.new_block(brick_id=brick_id, names=_names(name_kor, name_eng, desc_kor, desc_eng),
                    template_brick=template_brick, textures=textures, specular=_file(specular, 'specular'),
                    recipe=_recipe(recipe), icon=_file(icon, '아이콘'))
    ctx.notify([tables.BRICK_TABLE, 'XML\\item_table.xml', 'terrain.png', 'terrain_specular.png', r['icon']])
    return r


@tool('content_new_furniture', 'content',
      'Add a new furniture / placeable object: model = copy of an existing furniture model (optionally with '
      'new textures) or a glTF file; behaviour (sit, sleep, light, container ...) copied from a template product; '
      'size computed from the model. Creates product + item + names + icon (+ recipe). Place it with world_place.',
      writes=True, **NAME_ARGS,
      template_product=INT('Existing product id whose behaviour to copy (products_list).'),
      model_name=STR('New model file name inside Object\\, letters/digits/_-. only, e.g. "my_chair.nwmt".'),
      copy_model=STR('Model entry to copy, e.g. "Object\\obj_chair001.nwmt" (see furniture_textures).', None),
      retexture=OBJ('For copy_model: {texture entry of the copied model: new image path}.', None),
      gltf=STR('Or a .glb/.gltf file to import as the model.', None),
      product_id=INT('New product id 1-1023 (default: first free).', None),
      item_id=INT('New item id (default: first free from 19300).', None),
      icon=STR('Icon image (default: none - set one for the inventory).', None),
      recipe=RECIPE)
def content_new_furniture(ctx, name_kor, name_eng, desc_kor, desc_eng, template_product, model_name, copy_model,
                          retexture, gltf, product_id, item_id, icon, recipe):
    c = ctx.creator
    if not copy_model and not gltf:
        raise StudioError('copy_model 또는 gltf 중 하나를 주세요')
    for v in (retexture or {}).values():
        _file(v, '새 텍스처')
    if copy_model and '\\' not in copy_model and '/' not in copy_model:
        copy_model = 'Object\\' + copy_model           # products_list / product.xml give bare file names
    product_id = product_id or c.free_product_ids()[0]
    item_id = item_id or c.free_item_ids()[0]
    r = c.new_furniture(product_id=product_id, item_id=item_id, names=_names(name_kor, name_eng, desc_kor, desc_eng),
                        template_product=template_product, model_name=model_name, gltf=_file(gltf, 'glTF'),
                        copy_model=copy_model, retexture=retexture or {}, icon=_file(icon, '아이콘'),
                        recipe=_recipe(recipe))
    ctx.notify([tables.PRODUCT_TABLE, 'XML\\item_table.xml', r['model']])
    return r


@tool('content_new_item', 'content',
      'Add a new item copied from a template item (food, potion, material, tool ...: effects and numbers follow '
      'the template) with its own name, icon and optional recipe.', writes=True, **NAME_ARGS,
      template_item=INT('Existing item id to copy (items_search).'),
      icon=STR('Icon image file.'),
      item_id=INT('New item id (default: first free from 19300).', None),
      recipe=RECIPE)
def content_new_item(ctx, name_kor, name_eng, desc_kor, desc_eng, template_item, icon, item_id, recipe):
    c = ctx.creator
    item_id = item_id or c.free_item_ids()[0]
    r = c.new_item(item_id=item_id, names=_names(name_kor, name_eng, desc_kor, desc_eng),
                   template_item=template_item, icon=_file(icon, '아이콘'), recipe=_recipe(recipe))
    ctx.notify(['XML\\item_table.xml', r['icon']])
    return r


@tool('content_new_sound', 'content',
      'Add a sound file and optionally use it as background music for weather/time slots or as a UI sound '
      '(see content_slots). To change an existing sound use sound_import / sound_edit instead.', writes=True,
      src=STR('Audio file (.wav/.ogg/.mp3).'),
      entry=STR('Entry name in the game, same extension, e.g. "Sound\\mod\\my_bgm.ogg".'),
      sky=ARR('BGM slots [[time, weather], ...].', {'type': 'array'}, None),
      ui=ARR('UI sound types, e.g. ["click"].', {'type': 'string'}, None),
      volume=INT('BGM volume (as in sky.xml).', None))
def content_new_sound(ctx, src, entry, sky, ui, volume):
    r = ctx.creator.new_sound(src=_file(src, '소리'), entry=entry, sky=[tuple(map(str, s)) for s in sky or []],
                              ui=list(ui or []), volume=volume)
    ctx.notify([entry, 'XML\\sky.xml', 'Gui\\sound.xml'])
    return r


@tool('content_added', 'content', 'Records this mod added or changed in the main tables (bricks, products, items, '
      'recipes, strings).')
def content_added(ctx):
    rows = ctx.creator.added()
    return {'count': len(rows), 'records': [{**r, 'key': list(r['key'])} for r in rows]}


# --------------------------------------------------------------------------- block textures

@tool('block_tile', 'content',
      'Look at one 128x128 tile of the block texture atlas (terrain.png / terrain_specular.png) and which bricks '
      'use it. Tile index = bricks_list tiles.', index=INT('Tile 0-255.'),
      layer=STR('Atlas.', 'diffuse', enum=('diffuse', 'specular')))
def block_tile(ctx, index, layer):
    im = B.tile(ctx.st, index, layer)
    users = [b['id'] for b in B.bricks(ctx.st) if index in b['tiles'].values()]
    p = render.save(im.convert('RGBA').resize((256, 256), 0).convert('RGB'), ctx.out_path(f'tile{index}_{layer}'))
    return Result({'index': index, 'layer': layer, 'used_by_bricks': users, 'image': p}, [p])


@tool('block_tile_set', 'content', 'Replace an atlas tile with an image (scaled to 128x128).', writes=True,
      index=INT('Tile 0-255.'), path=STR('Image file.'),
      layer=STR('Atlas.', 'diffuse', enum=('diffuse', 'specular')))
def block_tile_set(ctx, index, path, layer):
    B.tile_from_path(ctx.st, index, layer, _file(path, '이미지'))
    ctx.notify([tables.ATLAS[layer]])
    return {'index': index, 'layer': layer}


@tool('block_tile_adjust', 'content', 'Colour-adjust one atlas tile in place.', writes=True,
      index=INT('Tile 0-255.'), layer=STR('Atlas.', 'diffuse', enum=('diffuse', 'specular')),
      hue=NUM('Hue shift in degrees (-180..180).', 0), saturation=NUM('Saturation multiplier.', 1),
      brightness=NUM('Brightness multiplier.', 1), contrast=NUM('Contrast multiplier.', 1),
      tint=STR('Tint colour "#RRGGBB".', None), tint_amount=NUM('Tint strength 0..1.', 0))
def block_tile_adjust(ctx, index, layer, hue, saturation, brightness, contrast, tint, tint_amount):
    B.tile_adjust(ctx.st, index, layer, {'hue': hue, 'saturation': saturation, 'brightness': brightness,
                                         'contrast': contrast, 'tint': tint, 'tint_amount': tint_amount}, apply=True)
    ctx.notify([tables.ATLAS[layer]])
    return {'index': index, 'layer': layer}


@tool('block_tile_reset', 'content', 'Put an atlas tile back to the game original.', writes=True,
      index=INT('Tile 0-255.'), layer=STR('Atlas.', 'diffuse', enum=('diffuse', 'specular')))
def block_tile_reset(ctx, index, layer):
    B.tile_reset(ctx.st, index, layer)
    ctx.notify([tables.ATLAS[layer]])
    return {'index': index, 'layer': layer}


@tool('block_free_tiles', 'content', 'Empty atlas tiles no brick uses (where new block textures go).')
def block_free_tiles(ctx):
    return {'free': B.free_tiles(ctx.st)}


@tool('furniture_textures', 'content',
      'Model and textures of a furniture product (what the engine really loads), for retexturing with '
      'file_import / image_adjust or content_new_furniture(copy_model, retexture).',
      product=INT('Product id.', None), model=STR('Or a model entry, e.g. "Object\\obj_chair001.nwmt".', None))
def furniture_textures(ctx, product, model):
    if model is None:
        if product is None:
            raise StudioError('product 또는 model을 주세요')
        hit = next((p for p in B.products(ctx.st) if p['id'] == product), None)
        if not hit or not hit['entry']:
            raise StudioError(f'설치물 #{product}의 모델을 찾지 못했습니다')
        model = hit['entry']
    return {'model': ctx.st.canonical_name(model), **B.model_textures(ctx.st, model)}
