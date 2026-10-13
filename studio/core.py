"""NarewStudio state: settings, opened game install, opened mod project.

Reading a resource ("auto") = the project's whole-file override, else the game original,
then the project's record patches (merge/) applied on top.  The game folder is never written.
"""
from __future__ import annotations

import os
import threading

from narew import xmlmerge
from narew.modfmt import ModError, ModFolder, ModInfo, entry_key
from narew.paths import appdata, documents_dir, load_json, save_json

from .game import LOOSE, GameInstall

IMAGE = {'.png', '.jpg', '.jpeg', '.tga', '.bmp', '.gif', '.dds'}
AUDIO = {'.wav', '.ogg', '.mp3'}
FONT = {'.ttf', '.otf'}
MODEL = {'.nwmt', '.nwbt', '.nwat'}
BRICK = {'.iaf', '.bwr'}
TEXT = {'.xml', '.txt', '.fx', '.fxh', '.hlsl', '.ps', '.vs', '.shader', '.scene', '.effect',
        '.bat', '.ini', '.json', '.csv'} | MODEL


class StudioError(Exception):
    """A problem to show to the user as-is."""


def kind_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE:
        return 'image'
    if ext in AUDIO:
        return 'audio'
    if ext in FONT:
        return 'font'
    if ext in MODEL:
        return 'model'
    if ext in BRICK:
        return 'brick'
    if ext in TEXT:
        return 'text'
    return 'binary'


class Studio:
    def __init__(self):
        self.settings_path = os.path.join(appdata('NarewStudio'), 'settings.json')
        self.settings = load_json(self.settings_path, {})
        self.game: GameInstall | None = None
        self.project: ModFolder | None = None
        self._pfiles: dict[str, tuple[str, str]] | None = None
        self._pmerges: dict[str, tuple[str, str]] | None = None
        self.lock = threading.RLock()
        self.cache_dir = os.path.join(appdata('NarewStudio'), 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.listeners = []            # callables run after the project or game changes

    # ---------------------------------------------------------------- settings
    def save_settings(self):
        save_json(self.settings_path, self.settings)

    def default_projects_dir(self) -> str:
        return os.path.join(documents_dir(), 'NarewStudio', 'Projects')

    def restore(self):
        for key, fn in (('game', self.open_game), ('project', self.open_project)):
            v = self.settings.get(key)
            if v:
                try:
                    fn(v)
                except Exception:
                    pass

    def changed(self):
        for fn in list(self.listeners):
            fn()

    # ---------------------------------------------------------------- game
    def open_game(self, root: str):
        try:
            g = GameInstall(root)
        except Exception as ex:
            raise StudioError(str(ex))
        with self.lock:
            if self.game:
                self.game.close()
            self.game = g
        self.settings['game'] = g.root
        self.save_settings()

    def need_game(self) -> GameInstall:
        if not self.game:
            raise StudioError('먼저 게임 폴더를 선택하세요')
        return self.game

    # ---------------------------------------------------------------- project
    def open_project(self, root: str):
        if os.path.isfile(root) and os.path.basename(root).lower() == 'mod.json':
            root = os.path.dirname(root)
        m = ModFolder(root)
        try:
            m.read_info()
        except ModError as ex:
            raise StudioError(str(ex))
        with self.lock:
            self.project = m
            self._pfiles = self._pmerges = None
        self.settings['project'] = m.root
        recent = [p for p in self.settings.get('recent_projects', []) if p != m.root]
        self.settings['recent_projects'] = [m.root] + recent[:9]
        self.save_settings()

    def new_project(self, parent: str, info: ModInfo) -> str:
        root = os.path.join(parent, info.id)
        try:
            ModFolder.create(root, info)
        except ModError as ex:
            raise StudioError(str(ex))
        self.open_project(root)
        return root

    def close_project(self):
        with self.lock:
            self.project = None
            self._pfiles = self._pmerges = None
        self.settings.pop('project', None)
        self.save_settings()

    def need_project(self) -> ModFolder:
        if not self.project:
            raise StudioError('먼저 모드 프로젝트를 만들거나 여세요')
        return self.project

    def project_files(self) -> dict[str, tuple[str, str]]:
        with self.lock:
            if self.project is None:
                return {}
            if self._pfiles is None:
                self._pfiles = self.project.file_map()
            return self._pfiles

    def project_merges(self) -> dict[str, tuple[str, str]]:
        with self.lock:
            if self.project is None:
                return {}
            if self._pmerges is None:
                self._pmerges = self.project.merge_map()
            return self._pmerges

    def invalidate(self):
        with self.lock:
            self._pfiles = self._pmerges = None

    # ---------------------------------------------------------------- resources
    def exists(self, name: str) -> bool:
        k = entry_key(name)
        return k in self.project_files() or bool(self.game and self.game.locate(name))

    def read(self, name: str, source: str = 'auto') -> bytes:
        """source: 'game' original | 'base' override-or-game | 'auto' base + record patches."""
        k = entry_key(name)
        data = None
        if source in ('auto', 'base'):
            hit = self.project_files().get(k)
            if hit:
                with open(hit[1], 'rb') as f:
                    data = f.read()
        if data is None:
            g = self.need_game()
            loc = g.locate(name)
            if loc is None:
                raise StudioError(f'없는 파일: {name}')
            data = g.read_located(loc)
        if source == 'auto':
            patch = self.project_merges().get(k)
            if patch:
                data = self.apply_patch_file(name, data, patch[1])
        return data

    @staticmethod
    def apply_patch_file(name: str, data: bytes, patch_path: str) -> bytes:
        with open(patch_path, encoding='utf-8') as f:
            return xmlmerge.apply_bytes(data, [f.read()], name)

    def canonical_name(self, name: str) -> str:
        """Original-case entry name when the game has it, else the given name."""
        if self.game:
            loc = self.game.locate(name)
            if loc:
                return loc.entry.name
        hit = self.project_files().get(entry_key(name))
        if hit:
            return hit[0]
        return name.replace('/', '\\')

    def write(self, name: str, data: bytes) -> str:
        p = self.need_project().put(self.canonical_name(name), data)
        self.invalidate()
        return p

    def revert(self, name: str) -> bool:
        ok = self.need_project().remove(name)
        self.invalidate()
        return ok

    # record patches
    def read_patch(self, table: str) -> str | None:
        return self.need_project().read_merge(self.canonical_name(table))

    def upsert_record(self, table: str, element):
        p = self.need_project()
        name = self.canonical_name(table)
        p.write_merge(name, xmlmerge.upsert(p.read_merge(name), name, element))
        self.invalidate()

    def remove_record(self, table: str, tag: str, key: tuple) -> bool:
        p = self.need_project()
        name = self.canonical_name(table)
        text = p.read_merge(name)
        if not text:
            return False
        new = xmlmerge.remove(text, name, tag, key)
        if not xmlmerge.parse_patch(new):
            p.remove_merge(name)
        else:
            p.write_merge(name, new)
        self.invalidate()
        return True

    def status_of(self, name: str) -> str:
        k = entry_key(name)
        in_project = k in self.project_files()
        in_game = bool(self.game and self.game.locate(name))
        if in_project and in_game:
            return 'modified'
        if in_project:
            return 'new'
        if k in self.project_merges():
            return 'merged'
        return 'original'

    def locate_pack(self, name: str) -> str | None:
        """Pack the file lives in (or would be added to); '@loose' for loose game files."""
        if not self.game:
            return None
        loc = self.game.locate(name)
        if loc:
            return loc.pack
        return self.game.pack_for_new(name)

    @property
    def loose_marker(self) -> str:
        return LOOSE
