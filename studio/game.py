"""Read-only view of an Islet Online install: every pack entry plus loose data files."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from narew.modfmt import entry_key
from narew.pack import Entry, PackError, PackReader

CLIENT_EXES = ('IsletClient_release(x64).exe', 'IsletClient_release.exe')
LOOSE = '@loose'                       # pseudo pack name for files outside packs
LOOSE_DIRS = ('Language',)             # game data the client reads straight from disk


def looks_like_game(root: str) -> bool:
    data = os.path.join(root, 'data')
    return (bool(root) and os.path.isdir(data)
            and any(f.lower().endswith('.pack') for f in os.listdir(data))
            and any(os.path.isfile(os.path.join(root, e)) for e in CLIENT_EXES))


@dataclass
class LooseEntry:
    name: str
    path: str
    raw_size: int
    compressed: int = 0
    packed_size: int = 0
    attribute: int = 0
    offset: int = 0

    @property
    def md5(self) -> bytes:
        with open(self.path, 'rb') as f:
            return hashlib.md5(f.read()).digest()


@dataclass
class Located:
    pack: str                          # pack stem, or LOOSE
    entry: Entry | LooseEntry


class GameInstall:
    """Indexes data/*.pack and loose data folders.  When a '<file>.orig' backup exists (made by
    narew patch or the mod manager) the backup is read, so the studio always sees originals."""

    def __init__(self, root: str):
        if not looks_like_game(root):
            raise PackError(f'아일렛 온라인 설치 폴더가 아닙니다: {root}')
        self.root = os.path.abspath(root)
        self.data_dir = os.path.join(self.root, 'data')
        self.readers: dict[str, PackReader] = {}
        self.sources: dict[str, str] = {}
        self.index: dict[str, Located] = {}
        self.live_paths: dict[str, str] = {}
        for fn in sorted(os.listdir(self.data_dir)):
            if not fn.lower().endswith('.pack'):
                continue
            stem = fn[:-5].lower()
            path = os.path.join(self.data_dir, fn)
            self.live_paths[stem] = path
            src = self._preferred(stem)
            # indexed, then closed: an open pack could not be replaced by the mod manager
            r = PackReader(src, keep_open=False)
            self.readers[stem] = r
            self.sources[stem] = src
            for e in r:
                self.index.setdefault(entry_key(e.name), Located(stem, e))
        for d in LOOSE_DIRS:
            full = os.path.join(self.root, d)
            if not os.path.isdir(full):
                continue
            for fn in sorted(os.listdir(full)):
                p = os.path.join(full, fn)
                if not os.path.isfile(p) or fn.lower().endswith('.orig'):
                    continue
                src = p + '.orig' if os.path.isfile(p + '.orig') else p
                name = f'{d}\\{fn}'
                self.index[entry_key(name)] = Located(LOOSE, LooseEntry(name, src, os.path.getsize(src)))

    def close(self):
        for r in self.readers.values():
            r.close()
        self.readers.clear()

    def locate(self, name: str) -> Located | None:
        return self.index.get(entry_key(name))

    def read_live(self, name: str) -> bytes | None:
        """The entry as the game loads it right now, i.e. with applied mods (the live pack or loose
        file, not the .orig original the studio normally reads).  None if it cannot be read."""
        loc = self.locate(name)
        if loc is None:
            return None
        try:
            if loc.pack == LOOSE:
                with open(os.path.join(self.root, *loc.entry.name.split('\\')), 'rb') as f:
                    return f.read()
            live = os.path.join(self.data_dir, loc.pack + '.pack')
            if os.path.normcase(live) == os.path.normcase(self.sources[loc.pack]):
                return self.readers[loc.pack].read(loc.entry)
            with PackReader(live) as r:
                e = r.find(name)
                return r.read(e) if e else None
        except (OSError, PackError):
            return None

    def _preferred(self, stem: str) -> str:
        live = self.live_paths[stem]
        return live + '.orig' if os.path.isfile(live + '.orig') else live

    def _refresh(self, stem: str) -> bool:
        """Re-index a pack the mod manager replaced (or backed up to .orig) since it was indexed."""
        r = self.readers[stem]
        want = self._preferred(stem)
        if os.path.normcase(want) == os.path.normcase(r.path) and not r.changed():
            return False
        nr = PackReader(want, keep_open=False)
        self.readers[stem], self.sources[stem] = nr, want
        for e in nr:
            k = entry_key(e.name)
            cur = self.index.get(k)
            if cur is None or cur.pack == stem:
                self.index[k] = Located(stem, e)
        return True

    def read_located(self, loc: Located) -> bytes:
        if loc.pack != LOOSE and self._refresh(loc.pack):
            fresh = self.readers[loc.pack].find(loc.entry.name)
            if fresh is None:
                raise PackError(f'{loc.entry.name}: no longer in {loc.pack}.pack')
            return self.readers[loc.pack].read(fresh)
        if loc.pack == LOOSE:
            with open(loc.entry.path, 'rb') as f:
                return f.read()
        return self.readers[loc.pack].read(loc.entry)

    def read(self, name: str) -> bytes:
        loc = self.locate(name)
        if loc is None:
            raise KeyError(name)
        return self.read_located(loc)

    def pack_for_new(self, name: str) -> str | None:
        """Pack a new (not yet existing) entry would belong to: its top folder, or default."""
        top = name.replace('/', '\\').split('\\', 1)
        if len(top) == 1:
            return 'default' if 'default' in self.readers else None
        stem = top[0].lower()
        if stem in self.readers:
            return stem
        return LOOSE if top[0] in LOOSE_DIRS else None

    def loose_names(self) -> list[str]:
        return sorted(loc.entry.name for loc in self.index.values() if loc.pack == LOOSE)

    def pack_summary(self) -> list[dict]:
        out = []
        for stem, r in self.readers.items():
            t = r.trailer
            out.append({
                'pack': stem,
                'file': os.path.basename(self.sources[stem]),
                'from_backup': self.sources[stem].endswith('.orig'),
                'count': t.count,
                'size': t.file_size,
                'raw': sum(e.raw_size for e in r),
            })
        return out
