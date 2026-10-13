"""Keyed, text-preserving merge of game data tables.

Mods add or replace individual records (an item, a brick, a product, a string ...)
instead of shipping whole table files, so several mods can extend the same table.

A patch is a small XML file whose root holds the records to merge::

    <merge>
        <item id="19300"> ... </item>
        <string table="item_name" index="19300"> ... </string>
    </merge>

Each record is matched against the base table by the key attributes of its table
(``RULES``; default ``id``).  A match replaces that record's text span, anything else is
appended before the base root's closing tag.  Everything outside the touched spans keeps
its original bytes, whitespace and encoding.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from .pack import name_key

# entry key (engine case folding, '\\' separators) -> key attributes per record tag
RULES: dict[str, dict[str, tuple[str, ...]]] = {
    'xml\\item_table.xml': {'item': ('id',)},
    'xml\\brick_table.xml': {'brick': ('id',)},
    'xml\\product.xml': {'product': ('id',)},
    'xml\\crafting.xml': {'craft': ('id',)},
    'xml\\sky.xml': {'sky': ('time', 'weather')},
    'sky\\sky.xml': {'sky': ('time', 'weather')},
    'gui\\sound.xml': {'sound': ('type',)},
    'language\\string.xml': {'string': ('table', 'index')},
    'language\\asobi_string.xml': {'string': ('table', 'index')},
    'language\\happytuk_string.xml': {'string': ('table', 'index')},
}

_ATTR = re.compile(r'([A-Za-z_][\w.-]*)\s*=\s*("([^"]*)"|\'([^\']*)\')')


class MergeError(Exception):
    pass


def rule_for(entry_name: str, tag: str) -> tuple[str, ...]:
    return RULES.get(name_key(entry_name.replace('/', '\\')), {}).get(tag, ('id',))


def _unescape(v: str) -> str:
    return (v.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
             .replace('&apos;', "'").replace('&amp;', '&'))


def _attrs(start_tag: str) -> dict[str, str]:
    return {m.group(1): _unescape(m.group(3) if m.group(3) is not None else m.group(4))
            for m in _ATTR.finditer(start_tag)}


@dataclass
class Span:
    start: int        # index of '<'
    end: int          # index after the element's last character
    indent: str       # whitespace preceding the element on its line


@dataclass
class MergeReport:
    replaced: list[tuple] = field(default_factory=list)
    added: list[tuple] = field(default_factory=list)


_PROLOG_ITEM = re.compile(r'\s*(<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>)', re.S)
_ROOT = re.compile(r'\s*<([A-Za-z_][\w.-]*)\b[^>]*>')


def _root_bounds(text: str) -> tuple[str, int, int]:
    """(root tag, index after root start tag, index of root end tag)."""
    pos = 1 if text.startswith('﻿') else 0
    while True:                      # skip declaration, comments, processing instructions
        m = _PROLOG_ITEM.match(text, pos)
        if not m:
            break
        pos = m.end()
    rm = _ROOT.match(text, pos)
    if not rm:
        raise MergeError('표 XML의 루트 요소를 찾을 수 없습니다')
    root = rm.group(1)
    close = text.rfind(f'</{root}>')
    if close < 0:
        raise MergeError(f'루트 </{root}> 닫는 태그가 없습니다')
    return root, rm.end(), close


def index_records(text: str, tag: str, keys: tuple[str, ...]) -> tuple[dict[tuple, Span], int]:
    """Map key -> span for every <tag ...> record, and return the root close position."""
    root, body_start, body_end = _root_bounds(text)
    start_re = re.compile(r'<' + re.escape(tag) + r'(?=[\s/>])')
    out: dict[tuple, Span] = {}
    pos = body_start
    while True:
        m = start_re.search(text, pos, body_end)
        if not m:
            break
        gt = text.index('>', m.start())
        start_tag = text[m.start():gt + 1]
        if start_tag.endswith('/>'):
            end = gt + 1
        else:
            close = text.find(f'</{tag}>', gt + 1, body_end)
            if close < 0:
                raise MergeError(f'<{tag}> 닫는 태그가 없습니다 (위치 {m.start()})')
            end = close + len(tag) + 3
        a = _attrs(start_tag)
        key = tuple(a.get(k, '') for k in keys)
        line_start = text.rfind('\n', 0, m.start()) + 1
        indent = text[line_start:m.start()]
        if indent.strip():
            indent = ''
        out.setdefault(key, Span(m.start(), end, indent))
        pos = end
    return out, body_end


def serialize(el: ET.Element, indent: str, step: str, nl: str) -> str:
    """Element as text (attribute order kept), children one per line."""
    attrs = ''.join(f' {k}={quoteattr(v)}' for k, v in el.attrib.items())
    kids = list(el)
    text = (el.text or '').strip()
    if not kids and not text:
        return f'<{el.tag}{attrs}/>'
    if not kids:
        return f'<{el.tag}{attrs}>{escape(text)}</{el.tag}>'
    inner = ''.join(nl + indent + step + serialize(k, indent + step, step, nl) for k in kids)
    return f'<{el.tag}{attrs}>{escape(text)}{inner}{nl}{indent}</{el.tag}>'


def _detect_step(text: str) -> str:
    m = re.search(r'\n([ \t]+)<', text)
    if m and m.group(1).startswith('\t'):
        return '\t'
    if m:
        return m.group(1)[:4] if len(m.group(1)) >= 4 else m.group(1)
    return '\t'


def parse_patch(patch: str | bytes) -> list[ET.Element]:
    try:
        root = ET.fromstring(patch)
    except ET.ParseError as ex:
        raise MergeError(f'병합 파일 XML 오류: {ex}')
    return list(root)


def apply(base: str, records: list[ET.Element], entry_name: str,
          report: MergeReport | None = None) -> str:
    """Merge records into base text; returns the new text."""
    if not records:
        return base
    nl = '\r\n' if '\r\n' in base else '\n'
    step = _detect_step(base)
    by_tag: dict[str, list[ET.Element]] = {}
    for r in records:
        by_tag.setdefault(r.tag, []).append(r)
    text = base
    for tag, recs in by_tag.items():
        keys = rule_for(entry_name, tag)
        spans, body_end = index_records(text, tag, keys)
        default_indent = next(iter(spans.values())).indent if spans else step
        edits = []          # (start, end, replacement)
        appended = []
        seen_new = {}
        for r in recs:
            key = tuple(r.get(k, '') for k in keys)
            sp = spans.get(key)
            if sp is not None:
                edits.append((sp.start, sp.end, serialize(r, sp.indent, step, nl)))
                if report is not None:
                    report.replaced.append((tag,) + key)
            else:
                seen_new[key] = r          # last one wins inside one patch
        for key, r in seen_new.items():
            appended.append(default_indent + serialize(r, default_indent, step, nl))
            if report is not None:
                report.added.append((tag,) + key)
        edits.sort(key=lambda e: e[0])
        out, pos = [], 0
        for s, e, rep in edits:
            out.append(text[pos:s])
            out.append(rep)
            pos = e
        out.append(text[pos:])
        text = ''.join(out)
        if appended:
            _, _, close = _root_bounds(text)
            line_start = text.rfind('\n', 0, close) + 1
            insert_at = line_start if not text[line_start:close].strip() else close
            block = nl.join(appended) + nl
            if insert_at == close:
                block = nl + block
            text = text[:insert_at] + block + text[insert_at:]
    return text


def apply_bytes(data: bytes, patches: list[str], entry_name: str, report: MergeReport | None = None) -> bytes:
    """Apply patch texts (lowest priority first) to a game file, keeping its encoding, BOM and
    newlines.  Characters the file's encoding cannot hold become &#NNNN; references."""
    from . import textcodec
    text, info = textcodec.decode(data)
    for p in patches:
        text = apply(text, parse_patch(p), entry_name, report)
    return textcodec.encode(text, info, errors='xmlcharrefreplace')


# --------------------------------------------------------------------------- patch files

def new_patch_text() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n<merge>\n</merge>\n'


def upsert(patch_text: str | None, entry_name: str, element: ET.Element) -> str:
    """Add or replace one record in a patch file (keyed by its table's rule)."""
    records = parse_patch(patch_text) if patch_text else []
    keys = rule_for(entry_name, element.tag)
    want = tuple(element.get(k, '') for k in keys)
    records = [r for r in records
               if not (r.tag == element.tag and tuple(r.get(k, '') for k in keys) == want)]
    records.append(element)
    return render_patch(records)


def remove(patch_text: str, entry_name: str, tag: str, key: tuple) -> str:
    keys = rule_for(entry_name, tag)
    records = [r for r in parse_patch(patch_text)
               if not (r.tag == tag and tuple(r.get(k, '') for k in keys) == tuple(key))]
    return render_patch(records)


def render_patch(records: list[ET.Element]) -> str:
    body = ''.join('\n\t' + serialize(r, '\t', '\t', '\n') for r in records)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<merge>{body}\n</merge>\n'


def find(text: str, entry_name: str, tag: str, key: tuple) -> ET.Element | None:
    """Parse one record of a base table (for use as a template)."""
    spans, _ = index_records(text, tag, rule_for(entry_name, tag))
    sp = spans.get(tuple(str(k) for k in key))
    if sp is None:
        return None
    return parse_record(text[sp.start:sp.end])


_XML_BAD = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')


def parse_record(fragment: str) -> ET.Element:
    """Parse one record; shipped tables contain control characters TinyXML tolerates."""
    try:
        return ET.fromstring(_XML_BAD.sub('', fragment))
    except ET.ParseError as ex:
        raise MergeError(f'레코드를 읽을 수 없습니다: {ex}')


def keys_in(text: str, entry_name: str, tag: str) -> set[tuple]:
    spans, _ = index_records(text, tag, rule_for(entry_name, tag))
    return set(spans)
