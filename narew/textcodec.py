"""Decode/encode game text files while preserving encoding, BOM and newline style."""
from __future__ import annotations

import re
from dataclasses import dataclass

_DECL = re.compile(rb'^\s*<\?xml[^>]*encoding\s*=\s*["\']([A-Za-z0-9_.-]+)["\']', re.I)
_ALIASES = {'euc-kr': 'cp949', 'euckr': 'cp949', 'ks_c_5601-1987': 'cp949', 'utf8': 'utf-8'}


@dataclass
class TextInfo:
    encoding: str      # python codec name
    bom: bool
    newline: str       # '\r\n' or '\n'

    def to_dict(self):
        return {'encoding': self.encoding, 'bom': self.bom,
                'newline': 'CRLF' if self.newline == '\r\n' else 'LF'}


def decode(data: bytes) -> tuple[str, TextInfo]:
    if data.startswith(b'\xef\xbb\xbf'):
        enc, bom, body = 'utf-8', True, data[3:]
    elif data.startswith(b'\xff\xfe'):
        enc, bom, body = 'utf-16-le', True, data[2:]
    elif data.startswith(b'\xfe\xff'):
        enc, bom, body = 'utf-16-be', True, data[2:]
    else:
        bom, body = False, data
        m = _DECL.match(data[:200])
        declared = _ALIASES.get(m.group(1).decode().lower(), m.group(1).decode().lower()) if m else None
        enc = None
        for cand in ([declared] if declared else []) + ['utf-8', 'cp949']:
            try:
                body.decode(cand)
                enc = cand
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if enc is None:
            enc = 'cp949'
    text = body.decode(enc, errors='replace')
    newline = '\r\n' if '\r\n' in text else '\n'
    return text.replace('\r\n', '\n'), TextInfo(enc, bom, newline)


def encode(text: str, info: TextInfo, errors: str = 'strict') -> bytes:
    """errors='strict' raises for characters the encoding cannot hold; 'xmlcharrefreplace'
    writes them as &#NNNN; (valid for XML data)."""
    text = text.replace('\r\n', '\n')
    if info.newline == '\r\n':
        text = text.replace('\n', '\r\n')
    body = text.encode(info.encoding, errors)
    if info.bom:
        bom = {'utf-8': b'\xef\xbb\xbf', 'utf-16-le': b'\xff\xfe', 'utf-16-be': b'\xfe\xff'}[info.encoding]
        body = bom + body
    return body
