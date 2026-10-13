"""On-disk mirror of pack entries (project overrides first) for file-based converters.

narew.convert resolves textures and skeletons through the file system, so models are
materialised under one root using their pack entry paths.  A file is rewritten only when
its source signature (game MD5 or project mtime/size) changes.
"""
from __future__ import annotations

import os
import threading

from narew.modfmt import check_rel_path, entry_key

from . import tables
from .core import Studio

SHARED_SKELETONS = ('Character\\F1\\f1.nwbt', 'Character\\M1\\m1.nwbt')


def get_mirror(st: Studio) -> 'Mirror':
    """One shared mirror per studio (the model viewer and world editor both use it)."""
    m = getattr(st, 'mirror', None)
    if m is None:
        m = st.mirror = Mirror(st, os.path.join(st.cache_dir, 'tree'))
    return m


class Mirror:
    def __init__(self, st: Studio, root: str):
        self.st = st
        self.root = root
        self.sig: dict[str, tuple] = {}
        self.lock = threading.Lock()
        os.makedirs(root, exist_ok=True)

    def signature(self, name: str) -> tuple | None:
        k = entry_key(name)
        hit = self.st.project_files().get(k)
        base = None
        if hit:
            s = os.stat(hit[1])
            base = ('p', s.st_mtime_ns, s.st_size)
        else:
            g = self.st.game
            loc = g.locate(name) if g else None
            if loc:
                base = ('g', loc.pack, loc.entry.name if loc.pack.startswith('@') else loc.entry.md5)
        if base is None:
            return None
        patch = self.st.project_merges().get(k)
        if patch:
            s = os.stat(patch[1])
            base += ('m', s.st_mtime_ns, s.st_size)
        return base

    def path(self, name: str) -> str:
        rel = check_rel_path(self.st.canonical_name(name))
        return os.path.join(self.root, *rel.split('/'))

    def ensure(self, name: str) -> str | None:
        sig = self.signature(name)
        if sig is None:
            return None
        p = self.path(name)
        k = entry_key(name)
        with self.lock:
            if self.sig.get(k) == sig and os.path.isfile(p):
                return p
            data = self.st.read(name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, p)
            self.sig[k] = sig
        return p

    def entry_of(self, path: str) -> str:
        rel = os.path.relpath(os.path.abspath(path), self.root)
        return self.st.canonical_name(rel.replace(os.sep, '\\'))

    def siblings(self, name: str, ext: str) -> list[str]:
        """Entries with extension ext in the same folder as name (game + project)."""
        d = tables.entry_dir(name).lower()
        out = {}
        g = self.st.game
        if g:
            for k, loc in g.index.items():
                if k.endswith(ext) and os.path.dirname(k) == d:
                    out[k] = loc.entry.name
        for k, (n, _) in self.st.project_files().items():
            if k.endswith(ext) and os.path.dirname(k) == d:
                out[k] = n
        return sorted(out.values(), key=str.lower)

    def ensure_model(self, name: str) -> str:
        """Model + its textures (engine rule) + candidate skeletons."""
        p = self.ensure(name)
        if p is None:
            raise FileNotFoundError(name)
        try:
            mats = tables.model_materials(self.st.read(name))
        except Exception:
            mats = []
        for m in mats:
            for ref in m['textures'].values():
                t = tables.engine_texture_entry(name, ref)
                if t:
                    self.ensure(t)
        for s in self.siblings(name, '.nwbt') + list(SHARED_SKELETONS):
            self.ensure(s)
        return p
