"""Any game resource: search, read/write text and XML, replace/export files, images, table records."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

from PIL import Image

from narew import xmlmerge
from narew.modfmt import check_rel_path, entry_key

from .. import imaging, svc_resources as R, textcodec
from ..core import StudioError, kind_of
from ..creator import LANGS, STRINGS
from . import render
from .registry import BOOL, INT, NUM, OBJ, Result, STR, tool

SOURCE = STR('Which version: "auto" = game original + this mod (what the game will load), '
             '"base" = without this mod\'s table patches, "game" = untouched original.', 'auto',
             enum=('auto', 'base', 'game'))


def all_names(st) -> dict[str, str]:
    names = {k: loc.entry.name for k, loc in st.need_game().index.items()}
    for k, (n, _) in st.project_files().items():
        names[k] = n
    return names


@tool('files_search', 'files',
      'Find game resources (16k files in 20 packs + Language\\) by name. Returns entry names to use with the '
      'other tools, kind (image/audio/text/model/brick/font/binary), pack and whether this mod changed them.',
      query=STR('Text in the path (case-insensitive); "*" and "?" wildcards allowed.', ''),
      kind=STR('Only this kind.', None, enum=('image', 'audio', 'text', 'model', 'brick', 'font', 'binary')),
      changed=BOOL('Only files this mod changed or added.', False),
      limit=INT('Max results.', 100))
def files_search(ctx, query, kind, changed, limit):
    st = ctx.st
    q = query.strip().lower().replace('/', '\\')
    rx = re.compile(re.escape(q).replace(r'\*', '.*').replace(r'\?', '.')) if q else None
    pf, pm = st.project_files(), st.project_merges()
    hits, total = [], 0
    for k, n in sorted(all_names(st).items()):
        if rx and not rx.search(k):
            continue
        if kind and kind_of(n) != kind:
            continue
        if changed and k not in pf and k not in pm:
            continue
        total += 1
        if len(hits) < limit:
            loc = st.game.locate(n)
            hits.append({'name': n, 'kind': kind_of(n), 'pack': st.locate_pack(n),
                         'size': loc.entry.raw_size if loc else os.path.getsize(pf[k][1]),
                         'status': st.status_of(n)})
    return {'total': total, 'shown': len(hits), 'files': hits}


@tool('file_info', 'files', 'Details of one resource: kind, pack, size, status, and image/audio/text facts.',
      name=STR('Entry name, e.g. "gui_skin\\19_Login_Create\\morenori.png".'))
def file_info(ctx, name):
    st = ctx.st
    data = st.read(name)
    out = {'name': st.canonical_name(name), 'kind': kind_of(name), 'pack': st.locate_pack(name),
           'size': len(data), 'status': st.status_of(name)}
    k = out['kind']
    try:
        if k == 'image':
            out['image'] = imaging.info(data)
        elif k == 'audio':
            from .. import audio
            i = audio.info(data)
            out['audio'] = {**i, 'seconds': round(i['frames'] / i['rate'], 3)}
        elif k in ('text', 'model'):
            text, ti = textcodec.decode(data)
            out['text'] = {'encoding': ti.encoding, 'bom': ti.bom, 'newline': repr(ti.newline),
                           'lines': text.count('\n') + 1}
    except Exception as ex:
        out['note'] = f'could not inspect: {ex}'
    return out


@tool('text_read', 'files',
      'Read a text/XML/shader/model-XML resource with line numbers. Use grep to find lines first in big tables.',
      name=STR('Entry name.'), source=SOURCE,
      start=INT('First line (1-based).', 1), lines=INT('Number of lines.', 300),
      grep=STR('Only lines containing this (case-insensitive regex); returns their numbers.', None))
def text_read(ctx, name, source, start, lines, grep):
    text, ti = R.read_text(ctx.st, name, source)
    rows = text.split('\n')
    if grep:
        rx = re.compile(grep, re.I)
        found = [(i + 1, r.rstrip('\r')) for i, r in enumerate(rows) if rx.search(r)]
        return {'name': name, 'total_lines': len(rows), 'matches': len(found),
                'lines': [f'{i}: {r[:300]}' for i, r in found[:lines]]}
    a = max(1, start)
    b = min(len(rows), a + max(1, lines) - 1)
    return {'name': ctx.st.canonical_name(name), 'encoding': ti.encoding, 'total_lines': len(rows),
            'from': a, 'to': b, 'text': '\n'.join(f'{i}: {rows[i - 1].rstrip(chr(13))}' for i in range(a, b + 1))}


@tool('text_write', 'files',
      'Replace a whole text resource in the mod (original encoding/BOM/newlines kept; XML is syntax-checked). '
      'For tables prefer table_upsert (record merge, mod-friendly); for small edits text_edit.', writes=True,
      name=STR('Entry name.'), text=STR('Full new text.'),
      force=BOOL('Save even if the XML check fails.', False))
def text_write(ctx, name, text, force):
    n = R.save_text(ctx.st, name, text, force)
    ctx.notify([name])
    return {'name': ctx.st.canonical_name(name), 'bytes': n, 'status': ctx.st.status_of(name)}


@tool('text_edit', 'files',
      'Exact string replacement inside a text resource (like an editor\'s find/replace), saved to the mod. '
      'old must match exactly once unless all=true.', writes=True,
      name=STR('Entry name.'), old=STR('Exact text to replace.'), new=STR('Replacement.'),
      all=BOOL('Replace every occurrence.', False))
def text_edit(ctx, name, old, new, all):
    text, _ = R.read_text(ctx.st, name, 'base')
    n = text.count(old)
    if n == 0:
        raise StudioError('바꿀 문자열을 찾지 못했습니다 (text_read로 정확한 내용을 확인하세요)')
    if n > 1 and not all:
        raise StudioError(f'문자열이 {n}번 나옵니다. 더 길게 지정하거나 all=true')
    R.save_text(ctx.st, name, text.replace(old, new) if all else text.replace(old, new, 1))
    ctx.notify([name])
    return {'name': ctx.st.canonical_name(name), 'replaced': n if all else 1}


@tool('file_import', 'files',
      'Replace a resource with a file from disk (images are converted to the slot\'s format; resize=true fits '
      'the original size; audio/fonts must have the same extension - for sounds prefer sound_import).',
      writes=True,
      name=STR('Entry name to replace (or a new entry to add).'), path=STR('Source file.'),
      resize=BOOL('Images: scale to the original size.', False))
def file_import(ctx, name, path, resize):
    notes = R.replace_from_path(ctx.st, name, path, resize)
    ctx.notify([name])
    return {'name': ctx.st.canonical_name(name), 'notes': notes, 'status': ctx.st.status_of(name)}


@tool('file_export', 'files', 'Write a resource to a file on disk (as_png converts images, e.g. .tga/.dds).',
      name=STR('Entry name.'), path=STR('Output path.'), source=SOURCE,
      as_png=BOOL('Convert an image to PNG.', False))
def file_export(ctx, name, path, source, as_png):
    return {'path': path, 'bytes': R.export(ctx.st, name, path, source, as_png)}


@tool('file_revert', 'files', 'Remove this mod\'s replacement of a file (the game original is used again).',
      writes=True, name=STR('Entry name.'))
def file_revert(ctx, name):
    ok = ctx.st.revert(name)
    ctx.notify([name])
    return {'name': name, 'removed': ok}


@tool('image_view', 'files', 'Look at an image resource (returned as a picture; big images are scaled down).',
      name=STR('Entry name of an image.'), source=SOURCE,
      max_size=INT('Longest side in pixels.', 512))
def image_view(ctx, name, source, max_size):
    data = ctx.st.read(name, source)
    im = imaging.open_image(data).convert('RGBA')
    w, h = im.size
    im.thumbnail((max_size, max_size), Image.NEAREST if max(w, h) <= 64 else Image.LANCZOS)
    if max(im.size) < 96:
        k = max(1, 192 // max(im.size))
        im = im.resize((im.width * k, im.height * k), Image.NEAREST)
    bg = Image.new('RGBA', im.size, (40, 40, 48, 255))
    bg.alpha_composite(im)
    p = render.save(bg.convert('RGB'), ctx.out_path('img_' + os.path.basename(name)))
    return Result({'name': ctx.st.canonical_name(name), 'size': [w, h], 'mode': imaging.info(data).get('mode'),
                   'image': p}, [p])


@tool('image_adjust', 'files', 'Colour-adjust an image resource in place (kept in its format, alpha kept).',
      writes=True, name=STR('Entry name.'),
      hue=NUM('Hue shift in degrees (-180..180).', 0), saturation=NUM('Saturation multiplier.', 1),
      brightness=NUM('Brightness multiplier.', 1), contrast=NUM('Contrast multiplier.', 1),
      tint=STR('Tint colour "#RRGGBB".', None), tint_amount=NUM('Tint strength 0..1.', 0))
def image_adjust(ctx, name, hue, saturation, brightness, contrast, tint, tint_amount):
    R.adjust_apply(ctx.st, name, {'hue': hue, 'saturation': saturation, 'brightness': brightness,
                                  'contrast': contrast, 'tint': tint, 'tint_amount': tint_amount})
    ctx.notify([name])
    return {'name': ctx.st.canonical_name(name), 'status': ctx.st.status_of(name)}


@tool('packs_list', 'files', 'The game\'s .pack files: entries, sizes, whether read from an .orig backup.')
def packs_list(ctx):
    return ctx.st.need_game().pack_summary()


# --------------------------------------------------------------------------- tables

def _default_tag(table: str) -> str | None:
    rules = xmlmerge.RULES.get(entry_key(table), {})
    return next(iter(rules), None)


@tool('table_records', 'tables',
      'Read records of a game XML table (item_table, brick_table, product, crafting, sky, Gui\\sound, '
      'Language\\string ...) as XML, with this mod\'s patches applied. Filter by key or text.',
      table=STR('Table entry, e.g. "XML\\item_table.xml".'),
      tag=STR('Record tag (default: the table\'s known record tag, e.g. item, brick, product, craft, string).', None),
      key=STR('Key value(s), comma-separated for multi-key tables (string: "item_name,19300"; sky: "time,weather").',
              None),
      query=STR('Only records whose XML contains this text (case-insensitive).', None),
      limit=INT('Max records.', 10), source=SOURCE)
def table_records(ctx, table, tag, key, query, limit, source):
    tag = tag or _default_tag(table)
    if not tag:
        raise StudioError('이 표의 레코드 태그를 tag로 지정하세요 (예: "item")')
    text = textcodec.decode(ctx.st.read(table, source))[0]
    keys = xmlmerge.rule_for(table, tag)
    spans, _ = xmlmerge.index_records(text, tag, keys)
    want = tuple(k.strip() for k in key.split(',')) if key else None
    out, total = [], 0
    q = query.lower() if query else None
    for k, sp in spans.items():
        if want and k != want:
            continue
        frag = text[sp.start:sp.end]
        if q and q not in frag.lower():
            continue
        total += 1
        if len(out) < limit:
            out.append({'key': dict(zip(keys, k)), 'xml': frag if len(frag) < 6000 else frag[:6000] + '\n…'})
    return {'table': ctx.st.canonical_name(table), 'tag': tag, 'key_attrs': list(keys), 'records_in_table': len(spans),
            'matches': total, 'records': out}


@tool('table_upsert', 'tables',
      'Add or replace one record of a game table through the mod\'s merge patch (the mod manager merges it with '
      'other mods record by record). Give the full record XML; its key attributes decide add vs replace. '
      'Tip: copy an existing record from table_records and change it.', writes=True,
      table=STR('Table entry, e.g. "XML\\item_table.xml".'),
      xml=STR('One record, e.g. <item id="19301">...</item>.'))
def table_upsert(ctx, table, xml):
    st = ctx.st
    if not st.exists(table):
        raise StudioError(f'없는 표입니다: {table}')
    el = xmlmerge.parse_record(xml.strip())
    keys = xmlmerge.rule_for(table, el.tag)
    missing = [k for k in keys if el.get(k) is None]
    if missing:
        raise StudioError(f'<{el.tag}>에 키 속성 {missing}이(가) 없습니다')
    text = textcodec.decode(st.read(table))[0]
    key = tuple(el.get(k) for k in keys)
    existed = key in xmlmerge.index_records(text, el.tag, keys)[0]
    st.upsert_record(table, el)
    ctx.notify([table])
    out = {'table': st.canonical_name(table), 'tag': el.tag, 'key': dict(zip(keys, key)),
           'action': 'replaced' if existed else 'added'}
    if entry_key(table) not in xmlmerge.RULES:
        out['note'] = f'이 표는 병합 규칙이 없어 키를 {keys}로 가정했습니다'
    return out


@tool('table_remove', 'tables',
      'Remove a record from this mod\'s patch (undo table_upsert / content created by this mod). '
      'Game records themselves cannot be deleted.', writes=True,
      table=STR('Table entry.'), tag=STR('Record tag.'),
      key=STR('Key value(s), comma-separated for multi-key tables.'))
def table_remove(ctx, table, tag, key):
    ok = ctx.st.remove_record(table, tag, tuple(k.strip() for k in key.split(',')))
    ctx.notify([table])
    return {'removed': ok}


@tool('string_get', 'tables', 'Text of a Language\\string.xml entry in every language.',
      table=STR('String table, e.g. "item_name", "item" (descriptions), "ui".'), index=INT('Index (item id ...).'))
def string_get(ctx, table, index):
    text = textcodec.decode(ctx.st.read(STRINGS))[0]
    el = xmlmerge.find(text, STRINGS, 'string', (table, str(index)))
    if el is None:
        return {'found': False}
    return {'found': True, **{c.tag: c.text or '' for c in el}}


@tool('string_set', 'tables',
      'Set a Language\\string.xml entry (record merge). Languages not given copy eng (or kor).', writes=True,
      table=STR('String table, e.g. "item_name".'), index=INT('Index.'),
      kor=STR('Korean text.'), eng=STR('English text (default: kor).', None),
      others=OBJ('Other languages, e.g. {"jpn": "..."} (cha rus thi jpn vn ph tw id es pt de fr).', None))
def string_set(ctx, table, index, kor, eng, others):
    others = others or {}
    bad = [k for k in others if k not in LANGS]
    if bad:
        raise StudioError(f'모르는 언어 {bad}. 쓸 수 있는 것: {LANGS}')
    el = ET.Element('string', {'table': table, 'index': str(index)})
    for lang in LANGS:
        ET.SubElement(el, lang).text = kor if lang == 'kor' else others.get(lang, eng or kor)
    ctx.st.upsert_record(STRINGS, el)
    ctx.notify([STRINGS])
    return {'table': table, 'index': index}


@tool('texture_make', 'files',
      'Make a simple texture PNG on disk (for new blocks / furniture retexturing / GUI) when you have no image: '
      'solid colour with optional noise and a pattern.',
      color=STR('Base colour "#RRGGBB".'), size=INT('Width = height in pixels.', 128),
      pattern=STR('Pattern.', 'none', enum=('none', 'bricks', 'planks', 'tiles', 'border', 'checker', 'stripes')),
      color2=STR('Second colour for the pattern lines (default: darker base).', None),
      noise=NUM('Per-pixel brightness noise 0..0.5.', 0.06), seed=INT('Noise seed.', 1),
      path=STR('Output .png (default: in the agent output folder).', None))
def texture_make(ctx, color, size, pattern, color2, noise, seed, path):
    import numpy as np

    def rgb(s):
        s = s.strip().lstrip('#')
        if not re.fullmatch(r'[0-9a-fA-F]{6}', s):
            raise StudioError(f'색은 "#RRGGBB" 형식으로: {s}')
        return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], np.float32)
    base = rgb(color)
    line = rgb(color2) if color2 else base * 0.6
    n = max(4, min(1024, size))
    img = np.tile(base, (n, n, 1))
    yy, xx = np.mgrid[:n, :n]
    u = max(1, n // 16)
    if pattern == 'bricks':
        row = yy // (n // 4)
        m = (yy % (n // 4) < u) | (((xx + (row % 2) * (n // 4)) % (n // 2)) < u)
    elif pattern == 'planks':
        m = (yy % (n // 4) < u) | ((xx + (yy // (n // 4)) * (n // 3)) % n < u)
    elif pattern == 'tiles':
        m = (yy % (n // 2) < u) | (xx % (n // 2) < u)
    elif pattern == 'border':
        m = (xx < u * 2) | (yy < u * 2) | (xx >= n - u * 2) | (yy >= n - u * 2)
    elif pattern == 'checker':
        m = ((xx // (n // 4)) + (yy // (n // 4))) % 2 == 1
    elif pattern == 'stripes':
        m = (yy // max(1, n // 8)) % 2 == 1
    else:
        m = np.zeros((n, n), bool)
    img[m] = line
    if noise:
        rng = np.random.default_rng(seed)
        img *= 1 + rng.uniform(-noise, noise, (n, n, 1)).astype(np.float32)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), 'RGB')
    p = path or ctx.out_path('texture', '.png')
    render.save(im, p)
    return Result({'path': p, 'size': n}, [p])


def check_entry(name: str) -> str:
    check_rel_path(name)
    return name.replace('/', '\\')


# --------------------------------------------------------------------------- packs (explorer)

def _outside_game(ctx, path: str) -> str:
    path = os.path.abspath(path)
    g = ctx.st.game
    if g and os.path.normcase(path).startswith(os.path.normcase(g.root) + os.sep):
        raise StudioError('게임 폴더 안에는 쓰지 않습니다. 다른 폴더를 고르세요 (모드는 모드 매니저로 적용)')
    return path


@tool('pack_extract', 'files',
      'Extract game pack entries (the originals, even if a mod is applied) to a folder: <out>\\<pack>\\<entry path>. '
      'Whole packs also get a <pack>.narew.json manifest for pack_build.',
      out=STR('Output folder (outside the game folder).'),
      pack=STR('Pack name, e.g. "gui_skin" (default: all packs).', None),
      filter=STR('Only entries matching this pattern, e.g. "Gui\\*.xml".', None),
      verify=BOOL('Check MD5 of every entry.', False))
def pack_extract(ctx, out, pack, filter, verify):
    import fnmatch
    import json as _json
    from narew.pack import name_key
    g = ctx.st.need_game()
    out = _outside_game(ctx, out)
    stems = [pack.lower().removesuffix('.pack')] if pack else sorted(g.readers)
    bad = [s for s in stems if s not in g.readers]
    if bad:
        raise StudioError(f'없는 팩 {bad}. 있는 것: {sorted(g.readers)}')
    files = size = 0
    for stem in stems:
        r = g.readers[stem]
        manifest = {'pack': stem + '.pack', 'entries': []}
        for e in r:
            if filter and not fnmatch.fnmatch(name_key(e.name), name_key(filter)):
                continue
            data = r.read(e, verify=verify)
            fp = os.path.join(out, stem, *check_entry(e.name).split('\\'))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, 'wb') as f:
                f.write(data)
            manifest['entries'].append({'name': e.name, 'attribute': e.attribute, 'md5': e.md5.hex()})
            files += 1
            size += len(data)
        if not filter:
            with open(os.path.join(out, stem + '.narew.json'), 'w', encoding='utf-8') as f:
                _json.dump(manifest, f, indent=1)
    return {'out': out, 'packs': stems, 'files': files, 'bytes': size}


@tool('pack_verify', 'files', 'Check every entry of game packs (MD5, readable, no duplicate names).',
      pack=STR('Pack name (default: all).', None))
def pack_verify(ctx, pack):
    from narew.pack import name_key
    g = ctx.st.need_game()
    stems = [pack.lower().removesuffix('.pack')] if pack else sorted(g.readers)
    report = {}
    for stem in stems:
        r = g.readers[stem]
        fails, n = [], 0
        for e in r:
            n += 1
            try:
                r.read(e, verify=True)
            except Exception as ex:
                fails.append(f'{e.name}: {ex}')
        if len({name_key(e.name) for e in r}) != n:
            fails.append('duplicate names (the engine rejects such a pack)')
        report[stem] = {'entries': n, 'problems': fails[:20], 'ok': not fails}
    return {'ok': all(v['ok'] for v in report.values()), 'packs': report}


@tool('pack_build', 'files',
      'Build a .pack from a folder (entry paths = paths inside the folder; a <folder>.narew.json manifest from '
      'pack_extract keeps the original order). Mods normally do not need this - the mod manager builds packs.',
      folder=STR('Folder with the files.'), out=STR('Output .pack path (outside the game folder).'))
def pack_build(ctx, folder, out):
    import contextlib
    import io as _io
    import types
    from narew import cli as narew_cli
    out = _outside_game(ctx, out)
    log = _io.StringIO()
    with contextlib.redirect_stdout(log):
        narew_cli.cmd_build(types.SimpleNamespace(folder=folder, manifest=None, out=out))
    return {'out': out, 'bytes': os.path.getsize(out), 'log': log.getvalue().strip()}
