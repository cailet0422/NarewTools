"""GUI layout files (gui.pack ``Gui\\*.xml``): read, lay out, and edit them in place.

A layout is a tree of ``<node type="dialog|image|button|extended_text|...">`` elements.  A node's
direct children describe it:

* ``<pos x y [center]>``     offset from the anchor given by ``<align x="left|center|right"
  y="top|center|bottom">`` inside the parent (default left/top)
* ``<size width height>``    ``<sizeoption width/height="parent_width|parent_height|normal">``
  stretches to the parent
* ``<texture [uv="wrap"]>path</texture>``  image under ``gui_skin\\``; ``<skinset width height
  left_x right_x top_y bottom_y>`` draws it as a 9-slice frame
* ``<text alignx aligny>``, ``<font size>``, ``<color font="0xAARRGGBB">`` for text nodes

The files are a mix of UTF-8, EUC-KR and no declaration, some with a BOM, and not all are strict
XML, so this module tokenises tags itself and changes only the characters it has to (the rest of
the file stays byte-for-byte as it was).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as xml_escape

_TOKEN = re.compile(r'<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<!.*?>'
                    r'|</\s*([\w:.-]+)\s*>'
                    r'|<\s*([\w:.-]+)((?:[^<>"\']|"[^"]*"|\'[^\']*\')*?)(/?)\s*>', re.S)
_ATTR = re.compile(r'([\w:.-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')
SKIN_DIR = 'gui_skin'


@dataclass
class Elem:
    tag: str
    attrs: dict[str, str]
    start: int                    # start of the start tag
    tag_end: int                  # end of the start tag
    end: int = -1                 # end of the element (after the end tag)
    inner_end: int = -1           # start of the end tag (-1 for <x/>)
    text: str = ''                # direct text content of leaf elements, as written (entities kept)
    children: list['Elem'] = field(default_factory=list)
    parent: 'Elem | None' = None

    def child(self, tag: str) -> 'Elem | None':
        return next((c for c in self.children if c.tag == tag), None)

    def nodes(self) -> list['Elem']:
        return [c for c in self.children if c.tag == 'node']


def parse(text: str) -> Elem:
    """Tolerant tag tree: unclosed tags are closed by their parent's end tag or the end of text."""
    root = Elem('#document', {}, 0, 0)
    stack = [root]
    for m in _TOKEN.finditer(text):
        end_tag, start_tag, attr_text, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if end_tag:
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == end_tag:
                    for e in stack[i:]:
                        e.end = m.end() if e is stack[i] else m.start()
                        e.inner_end = m.start()
                    if not stack[i].children:
                        stack[i].text = text[stack[i].tag_end:m.start()].strip()
                    del stack[i:]
                    break
            continue
        if not start_tag:
            continue                                    # comment / declaration
        attrs = {a.group(1): a.group(3) if a.group(3) is not None else a.group(4) for a in _ATTR.finditer(attr_text or '')}
        e = Elem(start_tag, attrs, m.start(), m.end(), parent=stack[-1])
        stack[-1].children.append(e)
        if selfclose:
            e.end = m.end()
        else:
            stack.append(e)
    for e in stack[1:]:
        e.end = e.inner_end = len(text)
    root.end = root.inner_end = len(text)
    return root


def all_nodes(root: Elem) -> list[Elem]:
    out = []

    def walk(e):
        for c in e.children:
            if c.tag == 'node':
                out.append(c)
            walk(c)
    walk(root)
    return out


def node_index(root: Elem, node: Elem) -> int:
    """A node's place in document order; edits here never add or remove nodes, so the index
    finds the same node again after re-parsing."""
    return next(i for i, n in enumerate(all_nodes(root)) if n is node)


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class Box:
    node: Elem
    x: float
    y: float
    w: float
    h: float
    visible: bool                 # own visible flag and all parents'
    depth: int


def layout(root: Elem, screen_w: int, screen_h: int, image_size=None) -> list[Box]:
    """Absolute rectangles for every node, in document order (= drawing order).
    image_size(path) -> (w, h) | None gives a texture's size for nodes without <size>."""
    out: list[Box] = []

    def place(e: Elem, px, py, pw, ph, vis, depth):
        size, sopt = e.child('size'), e.child('sizeoption')
        w = _num(size.attrs.get('width'), None) if size is not None else None
        h = _num(size.attrs.get('height'), None) if size is not None else None
        tex = e.child('texture')
        if (w is None or h is None) and tex is not None and image_size:
            s = image_size(tex.text)
            if s:
                w = s[0] if w is None else w
                h = s[1] if h is None else h
        if sopt is not None:
            if sopt.attrs.get('width', '').strip() == 'parent_width':
                w = pw
            if sopt.attrs.get('height', '').strip() == 'parent_height':
                h = ph
        w = pw if w is None else w
        h = ph if h is None else h
        al, pos = e.child('align'), e.child('pos')
        ax = (al.attrs.get('x', 'left') if al is not None else 'left').strip()
        ay = (al.attrs.get('y', 'top') if al is not None else 'top').strip()
        ox = _num(pos.attrs.get('x')) if pos is not None else 0.0
        oy = _num(pos.attrs.get('y')) if pos is not None else 0.0
        x = px + ox + (pw - w if ax == 'right' else (pw - w) / 2 if ax == 'center' else 0)
        y = py + oy + (ph - h if ay == 'bottom' else (ph - h) / 2 if ay == 'center' else 0)
        # top-level windows are visible="0" until the game opens them: show them as opened
        v = vis and (depth == 0 or e.attrs.get('visible', '1').strip() != '0')
        out.append(Box(e, x, y, w, h, v, depth))
        for c in e.children:
            visit(c, x, y, w, h, v, depth + 1)

    def visit(e, px, py, pw, ph, vis, depth):
        if e.tag == 'node':
            place(e, px, py, pw, ph, vis, depth)
        elif e.tag not in ('pos', 'size', 'align', 'texture', 'text', 'font', 'color', 'event', 'skinset'):
            for c in e.children:                       # wrappers such as <root>
                visit(c, px, py, pw, ph, vis, depth)

    for c in root.children:
        visit(c, 0, 0, screen_w, screen_h, True, 0)
    return out


def argb(v: str | None):
    """'0xAARRGGBB' -> (r, g, b, a) or None."""
    if not v:
        return None
    try:
        n = int(v.strip(), 16)
    except ValueError:
        return None
    return (n >> 16) & 255, (n >> 8) & 255, n & 255, (n >> 24) & 255


def texture_entry(path: str) -> str:
    return SKIN_DIR + '\\' + path.strip().replace('/', '\\')


# --------------------------------------------------------------------------- editing

def _set_attrs(text: str, el: Elem, values: dict[str, str]) -> str:
    """Change attributes inside el's start tag, keeping the file's spacing style; add missing ones."""
    tag = text[el.start:el.tag_end]
    for k, v in values.items():
        m = re.search(rf'(\b{re.escape(k)}\s*=\s*)("[^"]*"|\'[^\']*\')', tag)
        if m:
            q = m.group(2)[0]
            tag = tag[:m.start(2)] + f'{q}{v}{q}' + tag[m.end(2):]
        else:
            stripped = tag.rstrip()
            selfclose = stripped.endswith('/>')
            close = len(stripped) - (2 if selfclose else 1)
            tag = tag[:close].rstrip() + f' {k}="{v}"' + (' ' if selfclose else '') + tag[close:]
    return text[:el.start] + tag + text[el.tag_end:]


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f'{v:g}'


def set_pos(text: str, node: Elem, x: float, y: float) -> str:
    """Move a node: rewrite its <pos x y>, or insert one right after the node's start tag."""
    pos = node.child('pos')
    if pos is not None:
        ox, oy = node_pos(node)
        changed = {k: _fmt(v) for k, v, o in (('x', x, ox), ('y', y, oy)) if v != o}
        return _set_attrs(text, pos, changed) if changed else text
    line_start = text.rfind('\n', 0, node.start) + 1
    indent = text[line_start:node.start] if not text[line_start:node.start].strip() else ''
    ins = f'\n{indent}  <pos x="{_fmt(x)}" y="{_fmt(y)}"/>'
    return text[:node.tag_end] + ins + text[node.tag_end:]


def set_size(text: str, node: Elem, w: float, h: float) -> str:
    size = node.child('size')
    if size is not None:
        return _set_attrs(text, size, {'width': _fmt(w), 'height': _fmt(h)})
    line_start = text.rfind('\n', 0, node.start) + 1
    indent = text[line_start:node.start] if not text[line_start:node.start].strip() else ''
    ins = f'\n{indent}  <size width="{_fmt(w)}" height="{_fmt(h)}"/>'
    return text[:node.tag_end] + ins + text[node.tag_end:]


def node_pos(node: Elem) -> tuple[float, float]:
    p = node.child('pos')
    return (_num(p.attrs.get('x')), _num(p.attrs.get('y'))) if p is not None else (0.0, 0.0)


def node_label(node: Elem) -> str:
    t = node.attrs.get('type', '?').strip()
    bits = [t]
    if node.attrs.get('desc'):
        bits.append(node.attrs['desc'].strip())
    if node.attrs.get('id'):
        bits.append('#' + node.attrs['id'].strip())
    txt = node.child('text')
    if txt is not None and txt.text and t in ('extended_text', 'text', 'button'):
        bits.append(f'"{html.unescape(txt.text)[:20]}"')
    tex = node.child('texture')
    if tex is not None and tex.text:
        bits.append(tex.text.split('\\')[-1])
    return '  '.join(bits)


# --------------------------------------------------------------------------- more edits

def _indent_of(text: str, el: Elem) -> str:
    line_start = text.rfind('\n', 0, el.start) + 1
    pre = text[line_start:el.start]
    return pre if not pre.strip() else ''


def _newline(text: str) -> str:
    return '\r\n' if '\r\n' in text else '\n'


def _insert_child(text: str, node: Elem, snippet: str) -> str:
    """Insert one line right after node's start tag (a property element such as <text>)."""
    nl = _newline(text)
    return text[:node.tag_end] + f'{nl}{_indent_of(text, node)}  {snippet}' + text[node.tag_end:]


def node_text(node: Elem) -> str | None:
    t = node.child('text')
    return html.unescape(t.text) if t is not None else None


def set_text(text: str, node: Elem, value: str) -> str:
    """Change the words a text node shows (escaped for XML); add <text> when the node has none."""
    esc = xml_escape(value)
    t = node.child('text')
    if t is None:
        return _insert_child(text, node, f'<text alignx="center" aligny="center">{esc}</text>')
    if t.inner_end < 0:                                   # <text .../> -> <text ...>value</text>
        tag = text[t.start:t.tag_end].rstrip()
        tag = tag[:-2].rstrip() + '>'
        return text[:t.start] + tag + esc + '</text>' + text[t.tag_end:]
    return text[:t.tag_end] + esc + text[t.inner_end:]


def set_font_size(text: str, node: Elem, size: float) -> str:
    f = node.child('font')
    if f is not None:
        return _set_attrs(text, f, {'size': _fmt(size)})
    return _insert_child(text, node, f'<font size="{_fmt(size)}"></font>')


def set_text_color(text: str, node: Elem, rgba: tuple[int, int, int, int]) -> str:
    v = '0x{:02X}{:02X}{:02X}{:02X}'.format(rgba[3], rgba[0], rgba[1], rgba[2])
    c = node.child('color')
    if c is not None:
        return _set_attrs(text, c, {'font': v})
    return _insert_child(text, node, f'<color font="{v}"/>')


def set_texture(text: str, node: Elem, path: str) -> str:
    """Point a node at another gui_skin image (path relative to gui_skin, as the files write it)."""
    esc = xml_escape(path.strip())
    t = node.child('texture')
    if t is None:
        return _insert_child(text, node, f'<texture>{esc}</texture>')
    if t.inner_end < 0:
        tag = text[t.start:t.tag_end].rstrip()
        return text[:t.start] + tag[:-2].rstrip() + '>' + esc + '</texture>' + text[t.tag_end:]
    return text[:t.tag_end] + esc + text[t.inner_end:]


def set_node_attrs(text: str, node: Elem, values: dict[str, str]) -> str:
    """Attributes of the node's own start tag, e.g. {"visible": "0"} or {"touch": "1"}."""
    return _set_attrs(text, node, {k: str(v) for k, v in values.items()})


CONTAINERS = ('dialog', 'image', 'unfold', 'dragview', 'button')


def new_node(kind: str, x: int, y: int, w: int, h: int, texture: str | None = None,
             label: str | None = None, size: int = 14) -> list[str]:
    """Lines of a new element.  kind: image | button | extended_text."""
    attrs = ' touch="1"' if kind == 'button' else ''
    lines = [f'<node type="{kind}"{attrs} desc="NarewStudio">',
             f'  <pos x="{x}" y="{y}"/>',
             f'  <size width="{w}" height="{h}"/>']
    if texture:
        lines.append(f'  <texture>{xml_escape(texture)}</texture>')
    if label is not None:
        lines += [f'  <text alignx="center" aligny="center">{xml_escape(label)}</text>',
                  f'  <font size="{size}"></font>',
                  '  <color outline="0xFF000000" font="0xFFFFFFFF"/>']
    lines.append('</node>')
    return lines


def insert_node(text: str, parent: Elem, lines: list[str]) -> str:
    """Add an element as the last child of parent (indented like its other children)."""
    if parent.inner_end < 0:
        raise ValueError('cannot add children to a self-closing element')
    nl = _newline(text)
    indent = _indent_of(text, parent) + '  '
    close_line = text.rfind('\n', 0, parent.inner_end) + 1
    if text[close_line:parent.inner_end].strip():            # end tag shares a line with content
        block = nl + nl.join(indent + ln for ln in lines) + nl + _indent_of(text, parent)
        return text[:parent.inner_end] + block + text[parent.inner_end:]
    block = nl.join(indent + ln for ln in lines) + nl
    return text[:close_line] + block + text[close_line:]


def remove_node(text: str, node: Elem) -> str:
    """Remove an element with its line(s) when it sits on lines of its own."""
    start, end = node.start, node.end
    line_start = text.rfind('\n', 0, start) + 1
    if not text[line_start:start].strip():
        start = line_start
    line_end = text.find('\n', end)
    if line_end != -1 and not text[end:line_end].strip():
        end = line_end + 1
    return text[:start] + text[end:]


def is_ancestor(a: Elem, b: Elem) -> bool:
    """True when a contains b."""
    p = b.parent
    while p is not None:
        if p is a:
            return True
        p = p.parent
    return False
