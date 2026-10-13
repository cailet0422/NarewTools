"""Mod package format shared by NarewStudio (author) and NarewModManager (installer).

A mod is a folder (or a .zip of that folder) laid out as:

    mod.json            metadata, see ModInfo
    files/              resources, paths identical to pack entry names
                        (e.g. files/gui_skin/19_Login_Create/morenori.png)
    merge/              record-level additions to data tables (narew.xmlmerge),
                        e.g. merge/XML/item_table.xml, merge/Language/string.xml
    preview.png         optional thumbnail

Entry names inside packs use '\\' separators and the engine matches them
case-insensitively (ASCII A-Z only), so mods store '/' paths and every
comparison goes through ``entry_key``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field

from .pack import name_key

FORMAT_VERSION = 2          # 2: merge/ folder
MOD_JSON = 'mod.json'
FILES_DIR = 'files'
MERGE_DIR = 'merge'
PREVIEW = 'preview.png'
_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
MAX_ZIP_FILES = 100_000
MAX_ZIP_BYTES = 8 * 1024 ** 3


class ModError(Exception):
    pass


def entry_key(name: str) -> str:
    """Normalised identity of a resource path: '\\' separators, engine case folding."""
    return name_key(name.replace('/', '\\').strip('\\'))


def to_entry_name(rel: str) -> str:
    return rel.replace('/', '\\').strip('\\')


def check_rel_path(rel: str) -> str:
    """Validate a relative resource path from untrusted input; return it with '/' separators."""
    p = rel.replace('\\', '/')
    parts = p.split('/')
    if (not p or p.startswith('/') or ':' in p or any(x in ('', '.', '..') for x in parts)
            or any(ord(c) < 32 for c in p)):
        raise ModError(f'잘못된 경로: {rel!r}')
    return p


def slug(text: str) -> str:
    """ASCII id from a display name; names with no ASCII letters (e.g. Korean) get a stable hash."""
    s = re.sub(r'[^a-z0-9._-]+', '-', text.lower()).strip('-.')[:48]
    if len(s) < 3:
        s = (s + '-' if s else 'mod-') + hashlib.md5(text.encode('utf-8')).hexdigest()[:8]
    return s


@dataclass
class ModInfo:
    id: str
    name: str
    version: str = '1.0.0'
    author: str = ''
    description: str = ''
    game: str = 'islet-online'
    format: int = FORMAT_VERSION
    tags: list[str] = field(default_factory=list)

    def validate(self):
        if not _ID_RE.match(self.id):
            raise ModError(f'모드 id는 영문 소문자/숫자/._- 만 가능합니다: {self.id!r}')
        if not self.name.strip():
            raise ModError('모드 이름이 비어 있습니다')
        if self.format > FORMAT_VERSION:
            raise ModError(f'더 새로운 모드 포맷입니다 (format {self.format}). 도구를 업데이트하세요.')

    @classmethod
    def from_dict(cls, d: dict) -> 'ModInfo':
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        info = cls(**known)
        info.tags = [str(t) for t in (info.tags or [])]
        info.validate()
        return info

    def to_dict(self) -> dict:
        return asdict(self)


class ModFolder:
    """A mod laid out on disk (authoring project or installed mod)."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.files_root = os.path.join(self.root, FILES_DIR)
        self.merge_root = os.path.join(self.root, MERGE_DIR)

    # ---- metadata
    @property
    def info_path(self) -> str:
        return os.path.join(self.root, MOD_JSON)

    def exists(self) -> bool:
        return os.path.isfile(self.info_path)

    def read_info(self) -> ModInfo:
        try:
            with open(self.info_path, encoding='utf-8') as f:
                return ModInfo.from_dict(json.load(f))
        except FileNotFoundError:
            raise ModError(f'mod.json이 없습니다: {self.root}')
        except (json.JSONDecodeError, TypeError) as ex:
            raise ModError(f'mod.json을 읽을 수 없습니다: {ex}')

    def write_info(self, info: ModInfo):
        info.validate()
        os.makedirs(self.root, exist_ok=True)
        tmp = self.info_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(info.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.info_path)

    @classmethod
    def create(cls, root: str, info: ModInfo) -> 'ModFolder':
        m = cls(root)
        if m.exists():
            raise ModError(f'이미 모드 폴더입니다: {root}')
        m.write_info(info)
        os.makedirs(m.files_root, exist_ok=True)
        return m

    # ---- resources
    def path_of(self, entry: str) -> str:
        rel = check_rel_path(entry)
        return os.path.join(self.files_root, *rel.split('/'))

    def find(self, entry: str) -> str | None:
        """Case-insensitive lookup of a resource file inside files/."""
        p = self.path_of(entry)
        if os.path.isfile(p):
            return p
        want = entry_key(entry)
        for name, full in self.iter_files():
            if entry_key(name) == want:
                return full
        return None

    def iter_files(self):
        """Yield (entry name with '\\' separators, absolute path)."""
        if not os.path.isdir(self.files_root):
            return
        for dirpath, dirnames, filenames in os.walk(self.files_root):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, self.files_root)
                yield to_entry_name(rel), full

    def file_map(self) -> dict[str, tuple[str, str]]:
        """entry_key -> (entry name, absolute path)."""
        return {entry_key(n): (n, p) for n, p in self.iter_files()}

    def put(self, entry: str, data: bytes) -> str:
        existing = self.find(entry)
        p = existing or self.path_of(entry)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, p)
        return p

    def remove(self, entry: str) -> bool:
        p = self.find(entry)
        if not p:
            return False
        os.remove(p)
        # prune empty folders up to files/
        d = os.path.dirname(p)
        while os.path.normcase(d) != os.path.normcase(self.files_root) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
        return True

    # ---- table merges (merge/)
    def merge_path(self, entry: str) -> str:
        rel = check_rel_path(entry)
        return os.path.join(self.merge_root, *rel.split('/'))

    def iter_merges(self):
        """Yield (table entry name with '\\' separators, absolute path of the patch)."""
        if not os.path.isdir(self.merge_root):
            return
        for dirpath, dirnames, filenames in os.walk(self.merge_root):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn.lower().endswith('.tmp'):
                    continue
                full = os.path.join(dirpath, fn)
                yield to_entry_name(os.path.relpath(full, self.merge_root)), full

    def merge_map(self) -> dict[str, tuple[str, str]]:
        return {entry_key(n): (n, p) for n, p in self.iter_merges()}

    def read_merge(self, entry: str) -> str | None:
        hit = self.merge_map().get(entry_key(entry))
        if not hit:
            return None
        with open(hit[1], encoding='utf-8') as f:
            return f.read()

    def write_merge(self, entry: str, text: str):
        hit = self.merge_map().get(entry_key(entry))
        p = hit[1] if hit else self.merge_path(entry)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + '.tmp', 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(p + '.tmp', p)

    def remove_merge(self, entry: str) -> bool:
        hit = self.merge_map().get(entry_key(entry))
        if not hit:
            return False
        os.remove(hit[1])
        return True

    # ---- packaging
    def export_zip(self, out_path: str) -> int:
        info = self.read_info()
        n = 0
        tmp = out_path + '.tmp'
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr(MOD_JSON, json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
            pv = os.path.join(self.root, PREVIEW)
            if os.path.isfile(pv):
                z.write(pv, PREVIEW)
            for name, full in self.iter_files():
                z.write(full, FILES_DIR + '/' + name.replace('\\', '/'))
                n += 1
            for name, full in self.iter_merges():
                z.write(full, MERGE_DIR + '/' + name.replace('\\', '/'))
                n += 1
        os.replace(tmp, out_path)
        return n


def read_zip_info(zip_path: str) -> tuple[ModInfo, str]:
    """Return (info, prefix) where prefix is '' or 'Folder/' if the zip wraps one folder."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        cands = [n for n in names if n == MOD_JSON or (n.endswith('/' + MOD_JSON) and n.count('/') == 1)]
        if not cands:
            raise ModError('zip 안에 mod.json이 없습니다')
        top = sorted(cands, key=len)[0]
        prefix = top[:-len(MOD_JSON)]
        info = ModInfo.from_dict(json.loads(z.read(top).decode('utf-8-sig')))
        return info, prefix


def extract_zip(zip_path: str, dest: str) -> ModFolder:
    """Safely unpack a mod zip into dest (must not exist). Only mod.json, preview.png, files/ and merge/ are taken."""
    info, prefix = read_zip_info(zip_path)
    if os.path.exists(dest):
        raise ModError(f'이미 존재합니다: {dest}')
    tmp = dest + '.installing'
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    total = count = 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            for zi in z.infolist():
                if zi.is_dir() or not zi.filename.startswith(prefix):
                    continue
                rel = zi.filename[len(prefix):]
                if rel in (MOD_JSON, PREVIEW):
                    target = os.path.join(tmp, rel)
                elif rel.startswith((FILES_DIR + '/', MERGE_DIR + '/')):
                    top, _, sub = rel.partition('/')
                    sub = check_rel_path(sub)
                    target = os.path.join(tmp, top, *sub.split('/'))
                else:
                    continue
                count += 1
                total += zi.file_size
                if count > MAX_ZIP_FILES or total > MAX_ZIP_BYTES:
                    raise ModError('zip이 너무 큽니다')
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(zi) as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        os.makedirs(os.path.join(tmp, FILES_DIR), exist_ok=True)
        m = ModFolder(tmp)
        m.read_info()
        os.replace(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return ModFolder(dest)
