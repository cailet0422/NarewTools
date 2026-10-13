"""Reader/writer for NarewEngine2 ``.pack`` archives (Islet Online).

Layout (all little-endian), recovered from NarewEngine2_Release64.dll
(Nw::CFilePackage; see docs/01-pack-format.md for addresses):

    data area   : for each entry, [u32 size][size bytes]
                  size bytes are a zlib stream (compressed=1) or raw (compressed=0)
    directory   : for each entry
                    u8  attribute     build-time FILE_ATTRIBUTE (image downscale hint)
                    u8  compressed    1 = zlib, 0 = stored
                    u8  reserved0     always 0
                    u8  reserved1     always 0
                    u32 raw_size      uncompressed size
                    u32 packed_size   compressed size, 0 when stored
                    u64 offset        offset of the [u32 size] prefix
                    u8  md5[16]       MD5 of the uncompressed bytes
                    u16 name_len
                    char name[name_len]   ASCII/ANSI, '\\' separators, original case
    trailer (40): u64 file_size, u64 dir_offset, u64 data_end, u64 payload_bytes,
                  u32 count, u32 magic = 0x87020200

The engine lower-cases A-Z when indexing names, rejects duplicate names, and does
not verify MD5 on load.  The original packer compresses with zlib level 6 and
stores a file raw when compression does not make it smaller.
"""
from __future__ import annotations

import hashlib
import mmap
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import BinaryIO, Iterable, Iterator

MAGIC = 0x87020200
TRAILER = struct.Struct('<QQQQII')
ENTRY_HEAD = struct.Struct('<BBBBIIQ16sH')
NAME_ENCODING = 'cp949'  # engine uses ANSI char*; all shipped names are ASCII
ZLIB_LEVEL = 6


class PackError(Exception):
    pass


def name_key(name: str) -> str:
    """Lookup key exactly as the engine builds it: ASCII A-Z lower-cased only."""
    return ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in name)


@dataclass
class Entry:
    name: str
    attribute: int = 0
    compressed: int = 0
    raw_size: int = 0
    packed_size: int = 0
    offset: int = 0
    md5: bytes = b'\0' * 16
    reserved: tuple[int, int] = (0, 0)

    @property
    def stored_size(self) -> int:
        return self.packed_size if self.compressed else self.raw_size


@dataclass
class Trailer:
    file_size: int
    dir_offset: int
    data_end: int
    payload_bytes: int
    count: int
    magic: int


class PackReader:
    """Read-only view over a .pack file (memory-mapped).

    keep_open=False indexes the pack and closes it again; each read then opens the file briefly.
    Long-lived readers (NarewStudio) use it so Windows lets the mod manager replace the pack —
    an open or mapped file cannot be replaced there, whatever the share mode."""

    def __init__(self, path: str | os.PathLike, keep_open: bool = True):
        self.path = os.fspath(path)
        self._m = None
        self._f = open(self.path, 'rb')
        st = os.fstat(self._f.fileno())
        size = st.st_size
        self.sig = (st.st_size, st.st_mtime_ns)
        if size < TRAILER.size:
            self._f.close()
            raise PackError(f'{self.path}: too small to be a pack ({size} bytes)')
        self._m = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        t = Trailer(*TRAILER.unpack_from(self._m, size - TRAILER.size))
        if t.magic != MAGIC:
            self.close()
            raise PackError(f'{self.path}: bad magic {t.magic:#010x}')
        self.trailer = t
        self.entries: list[Entry] = []
        p = t.dir_offset
        end = size - TRAILER.size
        for _ in range(t.count):
            if p + ENTRY_HEAD.size > end:
                raise PackError(f'{self.path}: directory overruns trailer')
            a, c, r0, r1, raw, packed, off, md5, nl = ENTRY_HEAD.unpack_from(self._m, p)
            p += ENTRY_HEAD.size
            name = bytes(self._m[p:p + nl]).decode(NAME_ENCODING)
            p += nl
            self.entries.append(Entry(name, a, c, raw, packed, off, md5, (r0, r1)))
        self.dir_end = p
        self._index = {name_key(e.name): e for e in self.entries}
        if not keep_open:
            self.close()

    def close(self):
        try:
            if self._m is not None:
                self._m.close()
        finally:
            if self._f is not None:
                self._f.close()
            self._m = self._f = None

    def changed(self) -> bool:
        """True when the file on disk is no longer the one that was indexed (replaced/removed)."""
        try:
            s = os.stat(self.path)
        except OSError:
            return True
        return (s.st_size, s.st_mtime_ns) != self.sig

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def find(self, name: str) -> Entry | None:
        return self._index.get(name_key(name.replace('/', '\\')))

    def read_chunk(self, e: Entry) -> bytes:
        """The stored payload (zlib stream or raw bytes), without the size prefix."""
        if self._m is None:                                 # keep_open=False: open just for this read
            with open(self.path, 'rb') as f:
                f.seek(e.offset)
                head = f.read(4)
                size = struct.unpack('<I', head)[0] if len(head) == 4 else -1
                if size != e.stored_size:
                    raise PackError(f'{e.name}: size prefix {size} != directory {e.stored_size}')
                data = f.read(size)
            if len(data) != size:
                raise PackError(f'{e.name}: pack ends inside the entry')
            return data
        size, = struct.unpack_from('<I', self._m, e.offset)
        if size != e.stored_size:
            raise PackError(f'{e.name}: size prefix {size} != directory {e.stored_size}')
        return bytes(self._m[e.offset + 4:e.offset + 4 + size])

    def read(self, e: Entry | str, verify: bool = False) -> bytes:
        if isinstance(e, str):
            found = self.find(e)
            if found is None:
                raise KeyError(e)
            e = found
        chunk = self.read_chunk(e)
        data = zlib.decompress(chunk) if e.compressed else chunk
        if len(data) != e.raw_size:
            raise PackError(f'{e.name}: decoded {len(data)} bytes, expected {e.raw_size}')
        if verify and hashlib.md5(data).digest() != e.md5:
            raise PackError(f'{e.name}: MD5 mismatch')
        return data


@dataclass
class _Pending:
    name: str
    attribute: int = 0
    data: bytes | None = None          # uncompressed content to (re)encode
    chunk: bytes | None = None         # pre-encoded payload copied verbatim
    entry: Entry | None = None         # source entry for a verbatim copy


@dataclass
class PackWriter:
    """Builds a pack exactly the way Nw::CFilePackage::Save does."""
    level: int = ZLIB_LEVEL
    items: list[_Pending] = field(default_factory=list)
    _keys: dict[str, int] = field(default_factory=dict)

    def _put(self, item: _Pending):
        k = name_key(item.name)
        if k in self._keys:
            self.items[self._keys[k]] = item      # replace in place, keep order
        else:
            self._keys[k] = len(self.items)
            self.items.append(item)

    def add(self, name: str, data: bytes, attribute: int = 0):
        name = name.replace('/', '\\')
        name.encode(NAME_ENCODING)
        if len(name.encode(NAME_ENCODING)) > 0xFFFF:
            raise PackError(f'name too long: {name}')
        self._put(_Pending(name, attribute, data=data))

    def copy_from(self, reader: PackReader, e: Entry):
        """Copy an entry verbatim (no recompression)."""
        self._put(_Pending(e.name, e.attribute, chunk=reader.read_chunk(e), entry=e))

    def has(self, name: str) -> bool:
        return name_key(name.replace('/', '\\')) in self._keys

    def write(self, out: BinaryIO, progress=None) -> Trailer:
        entries: list[Entry] = []
        pos = 0
        for i, it in enumerate(self.items):
            if it.chunk is not None:
                src = it.entry
                e = Entry(it.name, it.attribute, src.compressed, src.raw_size,
                          src.packed_size, pos, src.md5)
                payload = it.chunk
            else:
                data = it.data
                comp = zlib.compress(data, self.level)
                if len(comp) < len(data):
                    e = Entry(it.name, it.attribute, 1, len(data), len(comp), pos)
                    payload = comp
                else:
                    e = Entry(it.name, it.attribute, 0, len(data), 0, pos)
                    payload = data
                e.md5 = hashlib.md5(data).digest()
            out.write(struct.pack('<I', len(payload)))
            out.write(payload)
            pos += 4 + len(payload)
            entries.append(e)
            if progress:
                progress(i + 1, len(self.items), it.name)
        dir_offset = pos
        for e in entries:
            nb = e.name.encode(NAME_ENCODING)
            out.write(ENTRY_HEAD.pack(e.attribute, e.compressed, 0, 0, e.raw_size,
                                      e.packed_size, e.offset, e.md5, len(nb)))
            out.write(nb)
            pos += ENTRY_HEAD.size + len(nb)
        t = Trailer(pos + TRAILER.size, dir_offset, dir_offset,
                    dir_offset - 4 * len(entries), len(entries), MAGIC)
        out.write(TRAILER.pack(t.file_size, t.dir_offset, t.data_end, t.payload_bytes,
                               t.count, t.magic))
        return t


def write_atomic(path: str, writer: PackWriter, progress=None) -> Trailer:
    tmp = path + '.tmp'
    try:
        with open(tmp, 'wb') as f:
            t = writer.write(f, progress)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)                  # never leave a half-written copy behind
        except OSError:
            pass
        raise
    return t


def iter_files(root: str, skip: Iterable[str] = ()) -> Iterator[tuple[str, str]]:
    """Yield (pack name with '\\' separators, absolute path) for files under root."""
    skip = set(skip)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if rel in skip:
                continue
            yield rel.replace(os.sep, '\\'), full
