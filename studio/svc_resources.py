"""Resource editing operations (any file type) and project packaging."""
from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET

from PIL import Image

from narew.modfmt import ModError, check_rel_path, extract_zip
from narew.paths import manager_mods_dir

from . import imaging, textcodec
from .core import IMAGE, Studio, StudioError, kind_of

_XML_DECL = re.compile(r'^\s*<\?xml[^>]*\?>')


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def convert_incoming(st: Studio, name: str, data: bytes, upload_name: str = '',
                     resize: bool = False) -> tuple[bytes, dict]:
    """Turn new bytes into what the slot needs (same format as the original)."""
    target = ext_of(name)
    src_ext = ext_of(upload_name) if upload_name else target
    kind = kind_of(name)
    notes: dict = {}
    try:
        original = st.read(name, 'game')
    except StudioError:
        original = None
    if kind == 'image':
        if target == '.dds':
            if src_ext != '.dds':
                raise StudioError('DDS 자리에는 .dds 파일만 넣을 수 있습니다')
            return data, notes
        try:
            fit = None
            if resize and original is not None:
                o = imaging.open_image(original)
                fit = (o.width, o.height)
            out, notes = imaging.convert_upload(data, target, original, fit)
        except Exception as ex:
            raise StudioError(f'이미지를 읽을 수 없습니다: {ex}')
        if src_ext != target:
            notes['converted'] = f'{src_ext} → {target}'
        return out, notes
    if kind in ('audio', 'font') and src_ext != target:
        raise StudioError(f'같은 형식({target}) 파일로 교체하세요. 고른 파일: {src_ext or "?"}')
    return data, notes


def replace_from_path(st: Studio, name: str, path: str, resize: bool = False) -> dict:
    check_rel_path(name)
    if not os.path.isfile(path):
        raise StudioError('파일을 찾을 수 없습니다')
    with open(path, 'rb') as f:
        raw = f.read()
    data, notes = convert_incoming(st, name, raw, path, resize)
    st.write(name, data)
    return notes


def read_text(st: Studio, name: str, source: str = 'auto') -> tuple[str, textcodec.TextInfo]:
    data = st.read(name, source)
    if b'\0' in data[:4096] and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise StudioError('텍스트 파일이 아닙니다')
    return textcodec.decode(data)


def check_xml(text: str) -> str | None:
    """None if well-formed enough for the engine's TinyXML, else a Korean error message."""
    m = _XML_DECL.match(text)
    body = text[m.end():] if m else text
    offset = text[:m.end()].count('\n') if m else 0
    body = re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f]', '', body)
    try:
        ET.fromstring('<__root__>' + body + '</__root__>')
    except ET.ParseError as ex:
        line, col = ex.position
        return f'XML 문법 오류 {line + offset}줄 {col}칸: {ex.msg}'
    return None


def save_text(st: Studio, name: str, text: str, force: bool = False) -> int:
    check_rel_path(name)
    try:
        _, info = textcodec.decode(st.read(name, 'base'))
    except StudioError:
        info = textcodec.TextInfo('utf-8', False, '\r\n')
    if ext_of(name) == '.xml' and not force:
        err = check_xml(text)
        if err:
            raise StudioError(err)
    try:
        data = textcodec.encode(text, info)
    except UnicodeEncodeError as ex:
        raise StudioError(f'{info.encoding}로 저장할 수 없는 글자가 있습니다: {text[ex.start:ex.end]!r}')
    st.write(name, data)
    return len(data)


def export(st: Studio, name: str, path: str, source: str = 'auto', as_png: bool = False) -> int:
    data = st.read(name, source)
    if as_png:
        data = imaging.to_png(data)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    return len(data)


def adjust_apply(st: Studio, name: str, params: dict):
    check_rel_path(name)
    if ext_of(name) not in IMAGE or ext_of(name) == '.dds':
        raise StudioError('조정할 수 없는 형식입니다')
    src = st.read(name)
    out = imaging.adjust(imaging.open_image(src), **params)
    st.write(name, imaging.encode_like(out, ext_of(name), src))


def work_path(st: Studio, name: str) -> str:
    p = st.need_project()
    rel = check_rel_path(st.canonical_name(name))
    return os.path.join(p.root, '_work', *rel.split('/'))


def external_open(st: Studio, name: str) -> str:
    wp = work_path(st, name)
    os.makedirs(os.path.dirname(wp), exist_ok=True)
    with open(wp, 'wb') as f:
        f.write(st.read(name))
    return wp


def external_apply(st: Studio, name: str) -> dict:
    wp = work_path(st, name)
    if not os.path.isfile(wp):
        raise StudioError('편집용 파일이 없습니다. 먼저 "외부 편집기로 열기"를 누르세요.')
    return replace_from_path(st, name, wp)


# ---------------------------------------------------------------------------- project

def set_preview(st: Studio, image_path: str):
    p = st.need_project()
    try:
        im = Image.open(image_path).convert('RGBA')
    except Exception as ex:
        raise StudioError(f'이미지를 읽을 수 없습니다: {ex}')
    im.thumbnail((512, 512))
    im.save(os.path.join(p.root, 'preview.png'), 'PNG')


def changes(st: Studio) -> list[dict]:
    out = []
    for k, (name, path) in sorted(st.project_files().items()):
        out.append({'name': name, 'status': st.status_of(name), 'kind': kind_of(name),
                    'pack': st.locate_pack(name), 'size': os.path.getsize(path), 'merge': False})
    for k, (name, path) in sorted(st.project_merges().items()):
        out.append({'name': name, 'status': 'merged', 'kind': 'merge', 'pack': st.locate_pack(name),
                    'size': os.path.getsize(path), 'merge': True})
    return out


def export_zip(st: Studio, path: str) -> int:
    if not path.lower().endswith('.zip'):
        path += '.zip'
    return st.need_project().export_zip(path)


def install_to_manager(st: Studio, replace: bool = False) -> str | None:
    """Copy the project into NarewModManager's mods folder. Returns None if it exists and not replace."""
    p = st.need_project()
    info = p.read_info()
    dest = os.path.join(manager_mods_dir(), info.id)
    if os.path.exists(dest) and not replace:
        return None
    tmp_zip = os.path.join(st.cache_dir, f'install-{info.id}.zip')
    p.export_zip(tmp_zip)
    try:
        if os.path.exists(dest):
            shutil.rmtree(dest)
        extract_zip(tmp_zip, dest)
    except ModError as ex:
        raise StudioError(str(ex))
    finally:
        if os.path.exists(tmp_zip):
            os.remove(tmp_zip)
    from modmanager.core import Manager       # same registration as the manager's own install
    Manager().add_to_order(info.id)
    return dest
