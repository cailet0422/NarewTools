"""Low-level reading/writing of NarewEngine2's XML asset files (.nwmt/.nwbt/.nwat).

The files are produced by a 3ds Max exporter and are *not* well-formed XML: most have several
top-level elements (``<header>``, ``<material_list>``, ...) after the declaration, and the text is
EUC-KR (cp949) without an ``encoding=`` attribute.  The engine reads them with TinyXML, which
accepts multiple roots; here we wrap the body in a synthetic root element instead.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

ENCODING = 'cp949'          # superset of EUC-KR; what the exporter wrote
SYNTHETIC_ROOT = '__nw__'

_DECL = re.compile(r'^\s*<\?xml[^>]*\?>', re.S)


def decode(data: bytes) -> str:
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8')
    return data.decode(ENCODING, errors='replace')


_BAD_CHARREF = re.compile(r'&#(x[0-9a-fA-F]+|[0-9]+);')
_CTRL = re.compile('[' + ''.join(map(chr, (*range(0, 9), 11, 12, *range(14, 32)))) + ']')
_GLUED_ATTR = re.compile(r'"(?=[A-Za-z_][\w.-]*\s*=)')


def _xml_char_ok(code: int) -> bool:
    return code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0x10FFFF


def _sanitize(text: str) -> str:
    """Repair the few TinyXML-tolerated defects found in the shipped files.

    * character references to control characters inside garbled texture paths (``&#x1D;``)
    * attributes written without a separating space (``z="0.02"link_bone = "2000"``)
    """
    def charref(m):
        v = m.group(1)
        code = int(v[1:], 16) if v[0] == 'x' else int(v)
        return m.group(0) if _xml_char_ok(code) else ''
    text = _BAD_CHARREF.sub(charref, text)
    text = _CTRL.sub('', text)
    return _GLUED_ATTR.sub('" ', text)


def parse_bytes(data: bytes) -> ET.Element:
    """Return a synthetic root whose children are the file's top-level elements.

    Files of the second animation flavour wrap everything in ``<root>``; that wrapper is
    removed so callers always see ``header``, ``*_list`` ... as direct children.
    """
    text = _DECL.sub('', decode(data), count=1)
    try:
        root = ET.fromstring(f'<{SYNTHETIC_ROOT}>{text}</{SYNTHETIC_ROOT}>')
    except ET.ParseError:
        root = ET.fromstring(f'<{SYNTHETIC_ROOT}>{_sanitize(text)}</{SYNTHETIC_ROOT}>')
    if len(root) == 1 and root[0].tag == 'root':
        inner = root[0]
        root = ET.Element(SYNTHETIC_ROOT, {'wrapped': 'root'})
        root.extend(list(inner))
    return root


def parse_file(path: str) -> ET.Element:
    with open(path, 'rb') as f:
        return parse_bytes(f.read())


_TAG = re.compile(r'<!--.*?-->|<\?.*?\?>|<(/)?([A-Za-z_][\w.:-]*)(?:"[^"]*"|\'[^\']*\'|[^>"\'])*?(/)?>', re.S)


def detect_indent(data: bytes) -> str:
    """Indentation unit of a file: a few files were written with 2 spaces instead of 4."""
    m = re.search(rb'\n( +)<', data)
    return m.group(1).decode() if m and m.group(1) in (b'  ', b'    ', b'\t') else '    '


def toplevel_spans(data: bytes) -> list[tuple[str, str]]:
    """Exact source text of every top-level element, in file order.

    Sections this toolkit does not decode (physics, helpers, special bones, ...) are written
    back verbatim from these spans, so their formatting (some were written with 2-space
    indentation by a different tool) survives a round trip unchanged.
    """
    text = _DECL.sub('', decode(data), count=1)
    out, depth, start, tag = [], 0, None, None
    for m in _TAG.finditer(text):
        name = m.group(2)
        if name is None:
            continue
        if m.group(1):
            depth -= 1
            if depth == 0 and start is not None:
                out.append((tag, text[start:m.end()]))
                start = None
        elif m.group(3):
            if depth == 0:
                out.append((name, m.group(0)))
        else:
            if depth == 0:
                start, tag = m.start(), name
            depth += 1
    return out


def floats(s: str | None) -> list[float]:
    return [float(t) for t in s.split()] if s else []


def ival(e: ET.Element, name: str, default: int = 0) -> int:
    v = e.get(name)
    return int(v) if v not in (None, '') else default


def read_tm(e: ET.Element | None) -> list[list[float]] | None:
    """Read a ``<transform>`` with four ``<tm row=.. value="a b c d"/>`` rows (row-major)."""
    if e is None:
        return None
    rows = [[0.0] * 4 for _ in range(4)]
    for tm in e.findall('tm'):
        rows[int(tm.get('row'))] = floats(tm.get('value'))
    return rows


# ---------------------------------------------------------------- writing

def fmt(v: float, digits: int = 5) -> str:
    s = f'{v:.{digits}f}'
    return s


def fmt_vec(vals, digits: int = 5) -> str:
    return ' '.join(fmt(v, digits) for v in vals)


def fmt_g(v: float, exp3: bool = False) -> str:
    """printf("%g").  ``exp3``: older MSVC runtimes print 3-digit exponents (9.4419e-005);
    739 shipped models use 2 digits, 244 use 3."""
    s = f'{v:g}'
    if exp3 and 'e' in s:
        mant, exp = s.split('e')
        s = f'{mant}e{exp[0]}{int(exp[1:]):03d}'
    return s


def detect_exp3(data: bytes) -> bool:
    return re.search(rb'\de[-+]\d{3}"', data) is not None


def serialize(top_level: list[ET.Element], declaration: str = '<?xml version="1.0" ?>',
              wrap_root: bool = False, indent: str = '    ') -> bytes:
    """Write elements like the exporter (4-space indentation, CRLF), encoded as cp949."""
    out = [declaration]
    level = 0
    if wrap_root:
        out.append('<root>')
        level = 1
    for e in top_level:
        if isinstance(e, str):          # verbatim source text of an untouched section
            out.append(e)
        else:
            _emit(e, level, out, indent)
    if wrap_root:
        out.append('</root>')
    return ('\r\n'.join(out) + '\r\n').encode(ENCODING, errors='replace')   # exporter wrote CRLF


def _attrs(e: ET.Element) -> str:
    return ''.join(f' {k}="{_esc(v)}"' for k, v in e.attrib.items())


def _esc(v: str) -> str:
    return (str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def _emit(e: ET.Element, level: int, out: list[str], indent: str = '    ') -> None:
    pad = indent * level
    text = (e.text or '').strip()
    if len(e) == 0:
        if e.text is not None or e.tag.endswith('_map'):   # <diffuse_map></diffuse_map> style
            out.append(f'{pad}<{e.tag}{_attrs(e)}>{_esc(text)}</{e.tag}>')
        else:
            out.append(f'{pad}<{e.tag}{_attrs(e)} />')
        return
    out.append(f'{pad}<{e.tag}{_attrs(e)}>')
    for c in e:
        _emit(c, level + 1, out, indent)
    out.append(f'{pad}</{e.tag}>')
