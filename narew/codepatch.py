"""Code patches: small, checked byte edits of the game's engine DLLs (mod format 3, ``patches/``).

Some things are not data at all - the view distance levels, for example, are constants inside
``COption::UpdateViewDistance`` - so a mod may carry patch files next to ``files/`` and ``merge/``:

    patches/<name>.json
    {
      "format": 1,
      "description": "...",
      "targets": [
        {
          "file": "IsletEngine_release(x64).dll",     one of ALLOWED_TARGETS, in the game folder
          "sha256": "8e4c...",                        optional: the untouched file must be exactly this
          "patches": [
            {"symbol": "?UpdateViewDistance@COption@Islet@@QEAAXXZ", "offset": 0,     or "rva": "0x12c7d0"
             "original": "8b91d81e0000...", "patched": "8b81d81e0000...", "note": "..."}
          ]
        }
      ]
    }

Rules that keep a patch from ever corrupting the game:

* ``original`` must match the bytes in the untouched file, byte for byte, and ``patched`` has the
  same length; ``sha256`` pins the whole file (use it whenever patches jump between each other).
* A patch may not touch bytes the loader rewrites (base relocations), and must stay inside a section's
  file data or the PE headers.
* A patch file is all or nothing, over all of its targets: if any patch of any target does not fit,
  nothing of the file is written (code put into one DLL may call into or read data of another).
* Only the engine libraries can be patched - never the client exe, Steam or third-party files.

The mod manager applies patch files from the untouched ``<dll>.orig`` backup on every apply, highest
priority first; a patch file that overlaps bytes a higher mod already changed is skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass, field

FORMAT = 1
ALLOWED_TARGETS = ('IsletEngine_release(x64).dll', 'IsletEngine_release.dll',
                   'NarewEngine2_Release64.dll', 'NarewEngine2_Release.dll')
_HEX = re.compile(r'^[0-9a-fA-F\s]*$')
MAX_PATCH = 4096


class CodePatchError(Exception):
    pass


def allowed_target(name: str) -> str | None:
    """Canonical spelling of an engine library name, or None if it may not be patched."""
    for t in ALLOWED_TARGETS:
        if t.lower() == name.strip().lower():
            return t
    return None


def _hex(value, what: str) -> bytes:
    if not isinstance(value, str) or not _HEX.match(value):
        raise CodePatchError(f'{what}: 16진수 문자열이 아닙니다')
    s = re.sub(r'\s+', '', value)
    if len(s) % 2:
        raise CodePatchError(f'{what}: 16진수 자릿수가 홀수입니다')
    return bytes.fromhex(s)


@dataclass
class Patch:
    original: bytes
    patched: bytes
    symbol: str | None = None
    offset: int = 0
    rva: int | None = None
    note: str = ''

    def where(self) -> str:
        if self.symbol:
            return f'{self.symbol}+0x{self.offset:x}'
        return f'rva 0x{self.rva:x}'


@dataclass
class Target:
    file: str
    patches: list[Patch]
    sha256: str | None = None


@dataclass
class PatchFile:
    name: str
    description: str = ''
    targets: list[Target] = field(default_factory=list)


def parse(text: str, name: str = '') -> PatchFile:
    try:
        d = json.loads(text)
    except json.JSONDecodeError as ex:
        raise CodePatchError(f'{name}: JSON을 읽을 수 없습니다 ({ex})')
    if not isinstance(d, dict) or not isinstance(d.get('targets'), list) or not d['targets']:
        raise CodePatchError(f'{name}: "targets" 목록이 없습니다')
    if int(d.get('format', FORMAT)) > FORMAT:
        raise CodePatchError(f'{name}: 더 새로운 코드 패치 형식입니다. 도구를 업데이트하세요.')
    pf = PatchFile(name=name, description=str(d.get('description', '')))
    for t in d['targets']:
        fname = allowed_target(str(t.get('file', '')))
        if not fname:
            raise CodePatchError(f'{name}: 패치할 수 없는 파일입니다: {t.get("file")!r} '
                                 f'(엔진 DLL만 가능: {", ".join(ALLOWED_TARGETS)})')
        sha = t.get('sha256')
        if sha is not None and not re.fullmatch(r'[0-9a-fA-F]{64}', str(sha)):
            raise CodePatchError(f'{name}: sha256 형식이 잘못됐습니다')
        patches = []
        for i, p in enumerate(t.get('patches') or []):
            what = f'{name} {fname} #{i + 1}'
            orig = _hex(p.get('original'), what + ' original')
            new = _hex(p.get('patched'), what + ' patched')
            if not orig or len(orig) != len(new) or len(orig) > MAX_PATCH:
                raise CodePatchError(f'{what}: original과 patched는 길이가 같아야 합니다 (1~{MAX_PATCH}바이트)')
            if 'symbol' in p:
                patch = Patch(orig, new, symbol=str(p['symbol']), offset=int(str(p.get('offset', 0)), 0))
            elif 'rva' in p:
                patch = Patch(orig, new, rva=int(str(p['rva']), 0))
            else:
                raise CodePatchError(f'{what}: 위치(symbol 또는 rva)가 없습니다')
            patch.note = str(p.get('note', ''))
            patches.append(patch)
        if not patches:
            raise CodePatchError(f'{name}: {fname}에 패치가 없습니다')
        pf.targets.append(Target(fname, patches, sha.lower() if sha else None))
    return pf


def load(path: str) -> PatchFile:
    with open(path, encoding='utf-8-sig') as f:
        return parse(f.read(), os.path.splitext(os.path.basename(path))[0])


def dumps(pf: PatchFile) -> str:
    out = {'format': FORMAT, 'description': pf.description, 'targets': []}
    for t in pf.targets:
        rows = []
        for p in t.patches:
            row = {'symbol': p.symbol, 'offset': hex(p.offset)} if p.symbol else {'rva': hex(p.rva)}
            row.update(original=p.original.hex(), patched=p.patched.hex())
            if p.note:
                row['note'] = p.note
            rows.append(row)
        tgt = {'file': t.file}
        if t.sha256:
            tgt['sha256'] = t.sha256
        tgt['patches'] = rows
        out['targets'].append(tgt)
    return json.dumps(out, ensure_ascii=False, indent=2)


class PEImage:
    """Just enough of a PE file: sections, named exports, base relocations."""

    def __init__(self, data: bytes):
        self.data = data
        if data[:2] != b'MZ':
            raise CodePatchError('PE 파일이 아닙니다')
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            raise CodePatchError('PE 파일이 아닙니다')
        nsec, = struct.unpack_from('<H', data, pe + 6)
        optsz, = struct.unpack_from('<H', data, pe + 20)
        opt = pe + 24
        magic, = struct.unpack_from('<H', data, opt)
        self.pe64 = magic == 0x20b
        self.image_base = struct.unpack_from('<Q', data, opt + 24)[0] if self.pe64 else \
            struct.unpack_from('<I', data, opt + 28)[0]
        self.size_of_headers, = struct.unpack_from('<I', data, opt + 60)
        dirs = opt + (112 if self.pe64 else 96)
        self.dirs = [struct.unpack_from('<II', data, dirs + 8 * i) for i in range(16)]
        self.sections = []
        for i in range(nsec):
            o = opt + optsz + 40 * i
            name = data[o:o + 8].rstrip(b'\0').decode('ascii', 'replace')
            vsz, va, rsz, raw = struct.unpack_from('<IIII', data, o + 8)
            self.sections.append((name, va, vsz, raw, rsz))
        self._exports = None
        self._relocs = None

    def rva_to_offset(self, rva: int, n: int = 1) -> int:
        if rva + n <= self.size_of_headers:
            return rva
        for _, va, vsz, raw, rsz in self.sections:
            # a section's file data (rsz) may run past its virtual size up to the file alignment
            if va <= rva and rva + n <= va + rsz:
                return raw + rva - va
        raise CodePatchError(f'rva 0x{rva:x}는 파일 안의 위치가 아닙니다')

    def exports(self) -> dict[str, int]:
        if self._exports is None:
            self._exports = {}
            rva, size = self.dirs[0]
            if rva:
                o = self.rva_to_offset(rva, 40)
                n_funcs, n_names, a_funcs, a_names, a_ords = struct.unpack_from('<IIIII', self.data, o + 20)
                fo = self.rva_to_offset(a_funcs, 4 * n_funcs)
                no = self.rva_to_offset(a_names, 4 * n_names)
                oo = self.rva_to_offset(a_ords, 2 * n_names)
                for i in range(n_names):
                    nrva, = struct.unpack_from('<I', self.data, no + 4 * i)
                    ordinal, = struct.unpack_from('<H', self.data, oo + 2 * i)
                    s = self.rva_to_offset(nrva)
                    name = self.data[s:self.data.index(b'\0', s)].decode('ascii', 'replace')
                    self._exports[name] = struct.unpack_from('<I', self.data, fo + 4 * ordinal)[0]
        return self._exports

    def relocated(self) -> list[tuple[int, int]]:
        """Sorted (rva, length) of every spot the loader rewrites."""
        if self._relocs is None:
            out = []
            rva, size = self.dirs[5]
            if rva:
                o = self.rva_to_offset(rva, size)
                end = o + size
                while o + 8 <= end:
                    page, block = struct.unpack_from('<II', self.data, o)
                    if block < 8:
                        break
                    for k in range((block - 8) // 2):
                        e, = struct.unpack_from('<H', self.data, o + 8 + 2 * k)
                        typ, off = e >> 12, e & 0xfff
                        if typ == 3:
                            out.append((page + off, 4))
                        elif typ == 10:
                            out.append((page + off, 8))
                    o += block
            self._relocs = sorted(out)
        return self._relocs

    def locate(self, p: Patch) -> int:
        """RVA of a patch."""
        if p.symbol:
            rva = self.exports().get(p.symbol)
            if rva is None:
                raise CodePatchError(f'DLL에 {p.symbol} 심볼이 없습니다')
            return rva + p.offset
        return p.rva

    def touches_reloc(self, rva: int, n: int) -> bool:
        import bisect
        rel = self.relocated()
        i = bisect.bisect_left(rel, (rva - 8, 0))
        for r, ln in rel[i:]:
            if r >= rva + n:
                break
            if r + ln > rva:
                return True
        return False


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan(data: bytes, target: Target, img: PEImage | None = None) -> list[tuple[int, Patch]]:
    """Check a target's patches against the untouched file; return [(file offset, patch)] or raise."""
    if target.sha256 and sha256(data) != target.sha256:
        raise CodePatchError(f'{target.file} 버전이 다릅니다 (게임 업데이트?). 이 패치는 다른 버전용입니다.')
    img = img or PEImage(data)
    out = []
    spans = []
    for p in target.patches:
        rva = img.locate(p)
        o = img.rva_to_offset(rva, len(p.original))
        if img.touches_reloc(rva, len(p.original)):
            raise CodePatchError(f'{target.file} {p.where()}: 로더가 다시 쓰는 주소(재배치)와 겹칩니다')
        if data[o:o + len(p.original)] != p.original:
            raise CodePatchError(f'{target.file} {p.where()}: 원본 바이트가 다릅니다 (게임 업데이트?)')
        if any(o < b and a < o + len(p.original) for a, b in spans):
            raise CodePatchError(f'{target.file} {p.where()}: 같은 파일 안의 패치끼리 겹칩니다')
        spans.append((o, o + len(p.original)))
        out.append((o, p))
    return out


def apply(data: bytes, target: Target) -> bytes:
    """Patched copy of ``data`` (all of the target's patches, or CodePatchError)."""
    buf = bytearray(data)
    for o, p in plan(data, target):
        buf[o:o + len(p.patched)] = p.patched
    return bytes(buf)


def spans(data: bytes, target: Target) -> list[tuple[int, int]]:
    return [(o, o + len(p.original)) for o, p in plan(data, target)]
