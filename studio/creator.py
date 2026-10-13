"""Create new game content: blocks, furniture, items, sounds.

Everything is written into the open mod project: new files under files/ and table records
under merge/ (narew.xmlmerge), so several mods can add content side by side.

Engine facts used here (IsletEngine_release(x64).dll, see docs/07-new-content.md):
* brick ids 1..254, table indexed directly (CBrickTable::GetType)          -> free ids only
* product ids are stored in a 1024-slot pointer array without bounds check
  (IProductManager::Load allocates 0x2000 bytes)                           -> ids 1..1023 only
* item ids 1..19999 (CItemTable::GetItem), crafting ids 0..2599 (ParsingNode rejects > 0xA27)
* product model path = "object\\" + <model> (IProductType::ParsingType)
* names come from language\\string.xml tables item_name / item, keyed by item id
Data conventions (observed in the shipped tables): brick items share the brick's id and use
icon "brick\\icon_brick_NNN.png" under Item\\Icon\\; other item icons live in Item\\Icon\\craft\\.
"""
from __future__ import annotations

import copy
import io
import ntpath
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from PIL import Image

from narew import xmlmerge
from narew.modfmt import check_rel_path

from . import imaging, svc_blocks, tables, textcodec
from .core import Studio, StudioError

ITEM_TABLE = 'XML\\item_table.xml'
CRAFT_TABLE = 'XML\\crafting.xml'
STRINGS = 'Language\\string.xml'
SKY_TABLES = ('XML\\sky.xml', 'Sky\\sky.xml')
UI_SOUNDS = 'Gui\\sound.xml'

BRICK_MAX = 254
LEGACY_BRICK_IDS = set(range(171, 192))        # used by old prefabs, avoid (docs/05 §4)
PRODUCT_MAX = 1023
ITEM_MAX = 19999
MOD_ITEM_START = 19300                           # above every shipped item id (max 19211)
CRAFT_MAX = 2599
LANGS = ('kor', 'eng', 'cha', 'rus', 'thi', 'jpn', 'vn', 'ph', 'tw', 'id', 'es', 'pt', 'de', 'fr')

CRAFT_STATIONS = [
    # (label, keyword, need object)
    ('가구 제작 (가구 작업대)', 'furniture', 'furniture'),
    ('도구 제작 (도구 작업대)', 'tool', 'tool'),
    ('바느질 (재봉틀)', 'sewing', 'clothes'),
    ('제련 (용광로)', 'smelting', 'smelting'),
    ('제재 (제재소)', 'sawmilling', 'sawmilling'),
    ('요리 (조리대)', 'cooking', 'cook'),
    ('기계 (가구 작업대)', 'machinery', 'furniture'),
    ('조합 (조합대)', 'compounding', 'compounding'),
    ('전자 (전자 작업대)', 'Electronics', 'electronics'),
    ('손으로 (작업대 없음)', 'tool', 'none'),
]


@dataclass
class Names:
    kor: str
    eng: str = ''
    desc_kor: str = ''
    desc_eng: str = ''

    def clean(self) -> 'Names':
        if not self.kor.strip():
            raise StudioError('한국어 이름을 입력하세요')
        return Names(self.kor.strip(), (self.eng or self.kor).strip(),
                     self.desc_kor.strip(), (self.desc_eng or self.desc_kor).strip())


@dataclass
class Recipe:
    station: int = 0                              # index into CRAFT_STATIONS
    ingredients: list[tuple[int, int]] = field(default_factory=list)   # (item id, count), up to 6
    count: int = 1
    level: int = 1
    exp: int = 10
    cooltime: int = 300


class Creator:
    def __init__(self, st: Studio):
        self.st = st

    # ------------------------------------------------------------------ tables
    def text(self, table: str) -> str:
        return textcodec.decode(self.st.read(table))[0]

    def keys(self, table: str, tag: str) -> set[int]:
        out = set()
        for k in xmlmerge.keys_in(self.text(table), table, tag):
            try:
                out.add(int(k[0]))
            except ValueError:
                pass
        return out

    def record(self, table: str, tag: str, key) -> ET.Element:
        el = xmlmerge.find(self.text(table), table, tag, key if isinstance(key, tuple) else (str(key),))
        if el is None:
            raise StudioError(f'{table}에 {tag} {key}이(가) 없습니다')
        return el

    # ------------------------------------------------------------------ ids
    def free_brick_ids(self) -> list[int]:
        used = self.keys(tables.BRICK_TABLE, 'brick')
        return [i for i in range(1, BRICK_MAX + 1) if i not in used and i not in LEGACY_BRICK_IDS]

    def free_product_ids(self) -> list[int]:
        used = self.keys(tables.PRODUCT_TABLE, 'product')
        return [i for i in range(1, PRODUCT_MAX + 1) if i not in used]

    def free_item_ids(self, start: int = MOD_ITEM_START) -> list[int]:
        used = self.keys(ITEM_TABLE, 'item')
        return [i for i in range(start, ITEM_MAX + 1) if i not in used]

    def free_craft_ids(self) -> list[int]:
        used = self.keys(CRAFT_TABLE, 'craft')
        return [i for i in range(1, CRAFT_MAX + 1) if i not in used]

    def items(self, type_value: str | None = None) -> list[dict]:
        """Items for pickers: id, kor/eng name, type."""
        names = self.string_names()
        out = []
        for m in re.finditer(r'<item id="(\d+)">(.*?)</item>', self.text(ITEM_TABLE), re.S):
            body = m.group(2)
            t = re.search(r'<type value="(\w+)"', body)
            ty = t.group(1) if t else ''
            if type_value and ty != type_value:
                continue
            iid = int(m.group(1))
            e = re.search(r'<e_name>(.*?)</e_name>', body)
            out.append({'id': iid, 'type': ty, 'name': names.get(iid) or (e.group(1) if e else str(iid))})
        return out

    def string_names(self) -> dict[int, str]:
        out = {}
        try:
            text = self.text(STRINGS)
        except StudioError:
            return out
        for m in re.finditer(r'<string table="item_name" index="(\d+)">\s*<kor>(.*?)</kor>', text, re.S):
            out[int(m.group(1))] = m.group(2)
        return out

    # ------------------------------------------------------------------ shared writers
    def _strings(self, item_id: int, names: Names):
        for table, kor, eng in (('item_name', names.kor, names.eng), ('item', names.desc_kor, names.desc_eng)):
            el = ET.Element('string', {'table': table, 'index': str(item_id)})
            for lang in LANGS:
                ET.SubElement(el, lang).text = kor if lang == 'kor' else eng
            self.st.upsert_record(STRINGS, el)

    def _recipe(self, recipe: Recipe | None, item_id: int, label: str) -> int | None:
        if recipe is None or not recipe.ingredients:
            return None
        free = self.free_craft_ids()
        if not free:
            raise StudioError('제작 레시피 번호가 모자랍니다 (엔진 한도 2599)')
        cid = free[-1] if free[-1] > 2093 else free[0]
        _, keyword, station = CRAFT_STATIONS[recipe.station]
        el = ET.Element('craft', {'id': str(cid)})
        ET.SubElement(el, 'name').text = label
        ET.SubElement(el, 'infor', {
            'keyword': keyword, 'level': str(recipe.level), 'probability': '100', 'item': str(item_id),
            'option_type1': 'none', 'option_data1': '0', 'option_type2': 'none', 'option_data2': '0',
            'count': str(recipe.count), 'exp': str(recipe.exp), 'cooltime': str(recipe.cooltime)})
        ET.SubElement(el, 'need', {'object': station, 'min': '1' if station != 'none' else '0',
                                   'max': '1' if station != 'none' else '0', 'item': '0'})
        ET.SubElement(el, 'fail', {'item': '0', 'count': '0'})
        ings = list(recipe.ingredients)[:6]
        for i in range(6):
            iid, cnt = ings[i] if i < len(ings) else (0, 0)
            ET.SubElement(el, f'resource{i + 1}', {'item': str(iid), 'count': str(cnt)})
        ET.SubElement(el, 'country', {'value': '0'})
        self.st.upsert_record(CRAFT_TABLE, el)
        return cid

    @staticmethod
    def _own_disassembly(item: ET.Element, craft_id: int | None, recipe: Recipe | None):
        """<stat disassembly> names the craft whose ingredients disassembling returns.  A copied
        template would hand out the template's materials, so point it at our own recipe.  As in the
        shipped tables only a recipe that makes exactly one item is used (else disassembling would
        return more than it cost); block items never disassemble."""
        stat = item.find('stat')
        if stat is not None and stat.get('disassembly') is not None:
            ok = craft_id and recipe is not None and recipe.count == 1
            stat.set('disassembly', str(craft_id) if ok else '0')

    def _write_png(self, entry: str, im: Image.Image):
        buf = io.BytesIO()
        im.save(buf, 'PNG', optimize=True)
        self.st.write(entry, buf.getvalue())

    @staticmethod
    def _load_image(path: str) -> Image.Image:
        try:
            im = Image.open(path)
            im.load()
            return im.convert('RGBA')
        except Exception as ex:
            raise StudioError(f'이미지를 읽을 수 없습니다: {path}: {ex}')

    @staticmethod
    def _icon_from(path_or_image) -> Image.Image:
        im = path_or_image if isinstance(path_or_image, Image.Image) else Creator._load_image(path_or_image)
        im = im.convert('RGBA')
        im.thumbnail((64, 64), Image.LANCZOS)
        canvas = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        canvas.paste(im, ((64 - im.width) // 2, (64 - im.height) // 2), im)
        return canvas

    # ------------------------------------------------------------------ blocks
    @staticmethod
    def _face(tex: Image.Image, size: int, origin, u, v, shade: float) -> Image.Image:
        """Draw texture square onto the parallelogram origin + s*u + t*v (s, t in 0..1)."""
        n = tex.width
        det = u[0] * v[1] - v[0] * u[1]
        ia, ib, ic, idd = v[1] / det, -v[0] / det, -u[1] / det, u[0] / det
        coeffs = (ia * n, ib * n, -(ia * origin[0] + ib * origin[1]) * n,
                  ic * n, idd * n, -(ic * origin[0] + idd * origin[1]) * n)
        face = tex.transform((size, size), Image.AFFINE, coeffs, resample=Image.BILINEAR)
        if shade != 1:
            r, g, b, a = face.split()
            face = Image.merge('RGBA', [c.point(lambda x: int(x * shade)) for c in (r, g, b)] + [a])
        return face

    @classmethod
    def cube_icon(cls, top: Image.Image, left: Image.Image, right: Image.Image, size: int = 64) -> Image.Image:
        """Isometric cube from three face textures (like the shipped 64x64 brick icons)."""
        s = size / 64
        big = size * 4                     # draw large, then downsample for clean edges
        k = 4 * s
        p = lambda x, y: (x * k, y * k)
        tex = [im.convert('RGBA').resize((128, 128), Image.LANCZOS) for im in (top, left, right)]
        out = Image.new('RGBA', (big, big), (0, 0, 0, 0))
        # corners: left (4,18) back (32,4) right (60,18) front (32,32); height 28
        out.alpha_composite(cls._face(tex[1], big, p(4, 18), p(28, 14), p(0, 28), 0.80))
        out.alpha_composite(cls._face(tex[2], big, p(32, 32), p(28, -14), p(0, 28), 0.62))
        out.alpha_composite(cls._face(tex[0], big, p(4, 18), p(28, -14), p(28, 14), 1.0))
        return out.resize((size, size), Image.LANCZOS)

    def new_block(self, *, brick_id: int, names: Names, template_brick: int,
                  textures: dict[str, str], specular: str | None = None,
                  recipe: Recipe | None = None, icon: str | None = None) -> dict:
        """textures: {'all': path} or any of {'top','side','bottom'} (missing ones fall back)."""
        st = self.st
        st.need_project()
        names = names.clean()
        if brick_id not in self.free_brick_ids():
            raise StudioError(f'블록 번호 {brick_id}은(는) 쓸 수 없습니다 (이미 있거나 예약됨)')
        if not textures:
            raise StudioError('블록 텍스처를 하나 이상 고르세요')
        top = textures.get('top') or textures.get('all') or textures.get('side')
        side = textures.get('side') or textures.get('all') or top
        bottom = textures.get('bottom') or textures.get('all') or side
        faces = {'top': top, 'side': side, 'bottom': bottom}
        free = svc_blocks.free_tiles(st)
        distinct = list(dict.fromkeys(faces.values()))
        if len(free) < len(distinct):
            raise StudioError('아틀라스에 빈 칸이 모자랍니다')
        tile_of, images = {}, {}
        for path, ti in zip(distinct, free):
            images[path] = self._load_image(path)
            svc_blocks.write_tile(st, ti, 'diffuse', images[path])
            spec = self._load_image(specular) if specular else Image.new('RGBA', (8, 8), (0, 0, 0, 255))
            svc_blocks.write_tile(st, ti, 'specular', spec)
            tile_of[path] = ti
        tiles = {'top': tile_of[top], 'bottom': tile_of[bottom],
                 'front': tile_of[side], 'back': tile_of[side], 'left': tile_of[side], 'right': tile_of[side]}
        # brick record from the template
        tb = copy.deepcopy(self.record(tables.BRICK_TABLE, 'brick', template_brick))
        tb.set('id', str(brick_id))
        tb.find('name').text = names.eng
        tb.find('e_name').text = names.kor
        stat = tb.find('stat')
        if stat is not None:
            stat.set('drop1', str(brick_id))
            stat.set('drop2', str(brick_id))
            stat.set('drop3', '0')
            stat.set('detect', '0')
            stat.set('locating', '0')
        geo = tb.find('geometry')
        avg = images[top].resize((1, 1), Image.BOX).getpixel((0, 0))
        geo.set('next', '0')
        for f, t in tiles.items():
            geo.set(f'tile_{f}', str(t))
        geo.set('color', '0xFF%02X%02X%02X' % avg[:3])
        st.upsert_record(tables.BRICK_TABLE, tb)
        # item record (brick items share the brick id)
        try:
            ti_el = copy.deepcopy(self.record(ITEM_TABLE, 'item', template_brick))
        except StudioError:
            ti_el = copy.deepcopy(self.record(ITEM_TABLE, 'item', 1))
        ti_el.set('id', str(brick_id))
        ti_el.find('name').text = names.eng
        ti_el.find('e_name').text = names.kor
        icon_name = f'icon_brick_{brick_id:03d}.png'
        ti_el.find('icon').text = 'brick\\' + icon_name
        ti_el.find('model').text = str(brick_id)
        cid = self._recipe(recipe, brick_id, names.kor)
        self._own_disassembly(ti_el, None, None)
        st.upsert_record(ITEM_TABLE, ti_el)
        icon_im = self._icon_from(icon) if icon else self.cube_icon(images[top], images[side], images[side])
        self._write_png('Item\\Icon\\brick\\' + icon_name, icon_im)
        self._strings(brick_id, names)
        return {'brick': brick_id, 'item': brick_id, 'tiles': tiles, 'craft': cid,
                'icon': 'Item\\Icon\\brick\\' + icon_name}

    # ------------------------------------------------------------------ furniture
    def model_bbox(self, model_entry: str) -> tuple[list[float], list[float]]:
        from narew.nwmodel import load_model, mesh_bbox
        from .mirror import get_mirror
        p = get_mirror(self.st).ensure_model(model_entry)
        m = load_model(p)
        lo, hi = [1e9] * 3, [-1e9] * 3
        for mesh in m.meshes:
            bb = mesh_bbox(mesh)
            for i in range(3):
                lo[i] = min(lo[i], bb[i])
                hi[i] = max(hi[i], bb[3 + i])
        if lo[0] > hi[0]:
            return [-0.5, 0, -0.5], [0.5, 1, 0.5]
        return lo, hi

    def new_furniture(self, *, product_id: int, item_id: int, names: Names, template_product: int,
                      model_name: str, gltf: str | None = None, copy_model: str | None = None,
                      retexture: dict[str, str] | None = None, icon: str | Image.Image | None = None,
                      recipe: Recipe | None = None) -> dict:
        """Model from a glTF file, or a copy of an existing furniture model (optionally retextured).

        model_name: file name inside Object\\ (e.g. "my_chair.nwmt").  retexture maps texture
        entry names of the copied model to new image paths (saved as new files beside the model).
        """
        from .svc_models import ModelService
        st = self.st
        st.need_project()
        names = names.clean()
        if product_id not in self.free_product_ids():
            raise StudioError(f'가구 번호 {product_id}은(는) 쓸 수 없습니다 (1~{PRODUCT_MAX}, 비어 있어야 함)')
        if item_id not in self.free_item_ids(1):
            raise StudioError(f'아이템 번호 {item_id}은(는) 이미 있습니다')
        model_name = model_name.strip()
        if not model_name.lower().endswith('.nwmt'):
            model_name += '.nwmt'
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.nwmt', model_name):
            raise StudioError('모델 파일 이름은 영문·숫자·_-. 만 쓰고 폴더 없이 지으세요 (예: my_chair.nwmt)')
        entry = 'Object\\' + model_name
        if st.exists(entry):
            raise StudioError(f'{entry}이(가) 이미 있습니다. 다른 이름을 쓰세요')
        tp = copy.deepcopy(self.record(tables.PRODUCT_TABLE, 'product', template_product))
        tmpl_model = tp.findtext('model') or ''
        tmpl_entry = 'Object\\' + tmpl_model if tmpl_model.lower().endswith('.nwmt') else None
        if gltf:
            ModelService(st).import_new_model(gltf, entry, tmpl_entry if tmpl_entry and st.exists(tmpl_entry) else None)
        elif copy_model:
            self._copy_model(copy_model, entry, retexture or {})
        else:
            raise StudioError('모델을 glTF로 가져오거나 기존 가구를 복사하세요')
        lo, hi = self.model_bbox(entry)
        tp.set('id', str(product_id))
        tp.find('name').text = names.kor
        tp.find('model').text = model_name
        for tag in ('animation', 'animation_on'):
            if tp.find(tag) is not None and not copy_model:
                tp.find(tag).text = 'none'
        mn, mx = tp.find('min'), tp.find('max')
        if mn is not None and mx is not None:
            for i, a in enumerate('xyz'):
                mn.set(a, f'{lo[i]:.4f}')
                mx.set(a, f'{hi[i]:.4f}')
        st.upsert_record(tables.PRODUCT_TABLE, tp)
        # item: clone the template product's item
        tmpl_item = None
        for m in re.finditer(r'<item id="(\d+)">(.*?)</item>', self.text(ITEM_TABLE), re.S):
            if '<type value="Product"' in m.group(2) and re.search(rf'<model>{template_product}</model>', m.group(2)):
                tmpl_item = int(m.group(1))
                break
        it = copy.deepcopy(self.record(ITEM_TABLE, 'item', tmpl_item if tmpl_item else 800))
        it.set('id', str(item_id))
        it.find('name').text = names.eng
        it.find('e_name').text = names.kor
        icon_name = f'icon_mod_{item_id}.png'
        it.find('icon').text = icon_name
        it.find('model').text = str(product_id)
        cid = self._recipe(recipe, item_id, names.kor)
        self._own_disassembly(it, cid, recipe)
        st.upsert_record(ITEM_TABLE, it)
        if icon is not None:
            self._write_png('Item\\Icon\\craft\\' + icon_name, self._icon_from(icon))
        self._strings(item_id, names)
        return {'product': product_id, 'item': item_id, 'model': entry, 'craft': cid,
                'icon': 'Item\\Icon\\craft\\' + icon_name, 'bbox': [lo, hi]}

    def _copy_model(self, src_entry: str, dst_entry: str, retexture: dict[str, str]):
        """Copy a .nwmt (and textures it needs), renaming replaced textures to new files."""
        st = self.st
        text, info = textcodec.decode(st.read(src_entry))
        dst_dir = os.path.dirname(dst_entry)
        stem = os.path.splitext(os.path.basename(dst_entry))[0]
        mats = svc_blocks.model_textures(st, src_entry)['materials']
        for m in mats:
            for tag, t in m['textures'].items():
                if not t['entry']:
                    continue
                if t['entry'] in retexture:
                    ext = os.path.splitext(t['entry'])[1].lower()
                    new_ref = f'{stem}_{tag.split("_")[0]}{ext}'
                    data = imaging.encode_like(self._load_image(retexture[t['entry']]), ext, st.read(t['entry']))
                    st.write(dst_dir + '\\' + new_ref, data)
                else:
                    # the copy may live in another folder: point untouched textures back at the originals
                    new_ref = ntpath.relpath(t['entry'], dst_dir)
                    if new_ref.lower() == t['ref'].replace('/', '\\').lower():
                        continue
                text = re.sub(rf'(<{tag}>)\s*{re.escape(t["ref"])}\s*(</{tag}>)',
                              lambda mo, r=new_ref: mo.group(1) + r + mo.group(2), text)
        st.write(dst_entry, textcodec.encode(text, info))

    # ------------------------------------------------------------------ generic items
    def new_item(self, *, item_id: int, names: Names, template_item: int, icon: str | Image.Image,
                 recipe: Recipe | None = None) -> dict:
        st = self.st
        st.need_project()
        names = names.clean()
        if item_id not in self.free_item_ids(1):
            raise StudioError(f'아이템 번호 {item_id}은(는) 이미 있습니다')
        it = copy.deepcopy(self.record(ITEM_TABLE, 'item', template_item))
        it.set('id', str(item_id))
        it.find('name').text = names.eng
        it.find('e_name').text = names.kor
        icon_name = f'icon_mod_{item_id}.png'
        it.find('icon').text = icon_name
        cid = self._recipe(recipe, item_id, names.kor)
        self._own_disassembly(it, cid, recipe)
        st.upsert_record(ITEM_TABLE, it)
        self._write_png('Item\\Icon\\craft\\' + icon_name, self._icon_from(icon))
        self._strings(item_id, names)
        return {'item': item_id, 'craft': cid, 'icon': 'Item\\Icon\\craft\\' + icon_name,
                'type': it.find('type').get('value') if it.find('type') is not None else ''}

    # ------------------------------------------------------------------ sounds
    def sky_slots(self) -> list[dict]:
        """(time, weather, name, current bgm) from XML\\sky.xml."""
        out = []
        for m in re.finditer(r'<sky time="(\d+)" weather="(\d+)"[^>]*>(.*?)</sky>', self.text(SKY_TABLES[0]), re.S):
            name = re.search(r'<name>(.*?)</name>', m.group(3))
            bgm = re.search(r'<bgm file="([^"]*)"', m.group(3))
            out.append({'time': m.group(1), 'weather': m.group(2), 'name': name.group(1) if name else '',
                        'bgm': bgm.group(1) if bgm else ''})
        return out

    def ui_sound_slots(self) -> list[dict]:
        return [{'type': m.group(1), 'file': m.group(2)}
                for m in re.finditer(r'<sound type\s*="([^"]*)"\s*file\s*="([^"]*)"', self.text(UI_SOUNDS))]

    def new_sound(self, *, src: str, entry: str, sky: list[tuple[str, str]] = (), ui: list[str] = (),
                  volume: float | None = None) -> dict:
        """Add an audio file and optionally use it as weather/time BGM or a UI sound."""
        st = self.st
        st.need_project()
        entry = entry.replace('/', '\\')
        check_rel_path(entry)
        ext = os.path.splitext(entry)[1].lower()
        if ext not in ('.wav', '.ogg', '.mp3'):
            raise StudioError('사운드 파일은 .wav, .ogg, .mp3만 쓸 수 있습니다')
        if os.path.splitext(src)[1].lower() != ext:
            raise StudioError(f'파일 형식이 다릅니다: 고른 파일과 같은 확장자({os.path.splitext(src)[1]})로 이름을 지으세요')
        with open(src, 'rb') as f:
            st.write(entry, f.read())
        used = []
        for table in SKY_TABLES:
            if not st.exists(table):
                continue
            for time, weather in sky:
                el = xmlmerge.find(self.text(table), table, 'sky', (str(time), str(weather)))
                if el is None:
                    continue
                bgm = el.find('bgm')
                if bgm is None:
                    bgm = ET.SubElement(el, 'bgm')
                bgm.set('file', entry)
                if volume is not None:
                    bgm.set('volume', f'{volume:g}')
                st.upsert_record(table, el)
                used.append(f'{table}: time {time} weather {weather}')
        for t in ui:
            el = xmlmerge.find(self.text(UI_SOUNDS), UI_SOUNDS, 'sound', (t,))
            if el is None:
                continue
            el.set('file', entry.lower())
            st.upsert_record(UI_SOUNDS, el)
            used.append(f'UI: {t}')
        return {'entry': entry, 'used': used}

    # ------------------------------------------------------------------ listing what this mod added
    def added(self) -> list[dict]:
        out = []
        for table, tag in ((tables.BRICK_TABLE, 'brick'), (tables.PRODUCT_TABLE, 'product'),
                           (ITEM_TABLE, 'item'), (CRAFT_TABLE, 'craft'), (STRINGS, 'string')):
            try:
                patch = self.st.read_patch(table)
            except StudioError:
                patch = None
            if not patch:
                continue
            for r in xmlmerge.parse_patch(patch):
                keys = xmlmerge.rule_for(table, r.tag)
                out.append({'table': table, 'tag': r.tag, 'key': tuple(r.get(k, '') for k in keys),
                            'label': r.findtext('e_name') or r.findtext('name') or r.findtext('kor') or ''})
        return out
