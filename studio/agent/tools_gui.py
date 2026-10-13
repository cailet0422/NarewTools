"""GUI layouts (gui.pack Gui\\*.xml): inspect nodes, preview, edit in place, save to the mod."""
from __future__ import annotations

import os
import re

from narew.modfmt import entry_key

from .. import guilayout as G, imaging, textcodec
from ..core import StudioError
from . import render
from .registry import ARR, BOOL, INT, Result, STR, tool

SCREEN = STR('Screen size the layout is laid out for.', '1920x1080', enum=('1920x1080', '1600x900', '1280x720'))


def _screen(s: str) -> tuple[int, int]:
    w, h = s.split('x')
    return int(w), int(h)


def _layouts(st) -> list[str]:
    names = {k: loc.entry.name for k, loc in st.need_game().index.items()
             if k.startswith('gui\\') and k.endswith('.xml') and k != 'gui\\sound.xml'}
    for k, (n, _) in st.project_files().items():
        if k.startswith('gui\\') and k.endswith('.xml') and k != 'gui\\sound.xml':
            names[k] = n
    return [names[k] for k in sorted(names)]


def _load(ctx, name):
    st = ctx.st
    if not entry_key(name).startswith('gui\\'):
        name = 'Gui\\' + name
    if not name.lower().endswith('.xml'):
        name += '.xml'
    name = st.canonical_name(name)
    text, info = textcodec.decode(st.read(name))
    return name, text, info


def _painter(ctx):
    key = ('gui_painter', repr(ctx.project_root()))
    p = ctx.cached(key, lambda: render.GuiPainter(ctx.st))
    p._img.clear()                    # images may have changed since the last call
    p._names = None
    return p


def _node_row(i, b, nodes_parent_index) -> dict:
    n = b.node
    tex = n.child('texture')
    fn = n.child('font')
    col = n.child('color')
    row = {'index': i, 'type': n.attrs.get('type', '').strip(), 'label': G.node_label(n), 'depth': b.depth,
           'parent': nodes_parent_index.get(id(n.parent)), 'abs': [round(b.x), round(b.y), round(b.w), round(b.h)],
           'pos': list(G.node_pos(n)), 'visible': b.visible}
    for k in ('id', 'desc', 'visible', 'touch'):
        if k in n.attrs:
            row['attr_' + k] = n.attrs[k]
    t = G.node_text(n)
    if t is not None:
        row['text'] = t[:120]
    if tex is not None and tex.text:
        row['texture'] = tex.text
    if fn is not None and fn.attrs.get('size'):
        row['font_size'] = fn.attrs.get('size')
    if col is not None and col.attrs.get('font'):
        row['color'] = col.attrs.get('font')
    al = n.child('align')
    if al is not None:
        row['align'] = {k: v for k, v in al.attrs.items()}
    return row


def _tree(ctx, name, text, screen):
    root = G.parse(text)
    nodes = G.all_nodes(root)
    painter = _painter(ctx)
    boxes = G.layout(root, *screen, lambda p: (lambda im: im.size if im else None)(painter.image(p)) if p else None)
    parent_index = {id(n): i for i, n in enumerate(nodes)}
    return root, nodes, boxes, parent_index


@tool('gui_list', 'gui', 'GUI screens (gui.pack Gui\\*.xml, 169 layouts) with whether this mod changed them.',
      query=STR('Text in the name.', None))
def gui_list(ctx, query):
    q = (query or '').lower()
    return [{'name': n, 'status': ctx.st.status_of(n)} for n in _layouts(ctx.st) if not q or q in n.lower()]


@tool('gui_tree', 'gui',
      'Nodes of a GUI layout in document order: index (used by gui_edit/gui_render), type, label, depth, parent, '
      'absolute box [x, y, w, h] on screen, local pos, text, texture, font size, colour. Hidden windows are shown '
      'as opened (like the editor).',
      name=STR('Layout, e.g. "Gui\\inventory.xml" (or just "inventory").'), screen=SCREEN,
      query=STR('Only nodes whose label/text/texture contains this.', None),
      max_depth=INT('Only nodes up to this depth.', None), limit=INT('Max rows.', 400))
def gui_tree(ctx, name, screen, query, max_depth, limit):
    name, text, _ = _load(ctx, name)
    root, nodes, boxes, pidx = _tree(ctx, name, text, _screen(screen))
    rows = []
    q = (query or '').lower()
    for i, b in enumerate(boxes):
        if max_depth is not None and b.depth > max_depth:
            continue
        r = _node_row(i, b, pidx)
        if q and q not in (r['label'] + ' ' + r.get('text', '') + ' ' + r.get('texture', '')).lower():
            continue
        rows.append(r)
    return {'name': name, 'status': ctx.st.status_of(name), 'nodes_total': len(nodes), 'shown': min(len(rows), limit),
            'nodes': rows[:limit]}


@tool('gui_render', 'gui',
      'Picture of a GUI layout drawn with the real gui_skin images, node index numbers on every element '
      '(highlight = indices drawn in yellow). crop="window" zooms on the drawn window.',
      name=STR('Layout.'), screen=SCREEN, highlight=ARR('Node indices to highlight.', {'type': 'integer'}, None),
      labels=BOOL('Draw node index numbers.', True), crop=STR('Area.', 'window', enum=('window', 'screen')),
      show_hidden=BOOL('Also draw nodes marked visible="0".', False))
def gui_render(ctx, name, screen, highlight, labels, crop, show_hidden):
    name, text, _ = _load(ctx, name)
    img, boxes, meta = _painter(ctx).render(G.parse(text), _screen(screen), show_hidden, labels,
                                             set(highlight or ()), crop)
    p = render.save(img, ctx.out_path('gui_' + os.path.basename(name)))
    return Result({'name': name, 'image': p, 'nodes': len(boxes), **meta}, [p])


def _rgba(v) -> tuple[int, int, int, int]:
    if isinstance(v, (list, tuple)):
        c = [int(x) for x in v] + [255] * (4 - len(v))
        return tuple(c[:4])
    s = str(v).strip().lstrip('#')
    if s.lower().startswith('0x') and len(s) == 10:            # engine form 0xAARRGGBB
        n = int(s, 16)
        return (n >> 16) & 255, (n >> 8) & 255, n & 255, (n >> 24) & 255
    if not re.fullmatch(r'[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', s):
        raise StudioError(f'색은 "#RRGGBB" 또는 "#RRGGBBAA": {v}')
    r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    return r, g, b, int(s[6:8], 16) if len(s) == 8 else 255


def apply_op(text: str, op: dict, screen) -> tuple[str, dict]:
    """One edit on the layout text; indices are those of gui_tree for the text before this op."""
    kind = op.get('op')
    nodes = G.all_nodes(G.parse(text))

    def node(key='index'):
        if key not in op:
            raise StudioError(f'{kind}: "{key}"가 필요합니다')
        i = int(op[key])
        if not 0 <= i < len(nodes):
            raise StudioError(f'{kind}: 노드 번호 {i}가 범위 밖입니다 (0..{len(nodes) - 1})')
        return nodes[i]
    if kind == 'move':
        return G.set_pos(text, node(), float(op['x']), float(op['y'])), {}
    if kind == 'move_by':
        n = node()
        x, y = G.node_pos(n)
        return G.set_pos(text, n, x + float(op.get('dx', 0)), y + float(op.get('dy', 0))), {}
    if kind == 'resize':
        return G.set_size(text, node(), float(op['w']), float(op['h'])), {}
    if kind == 'text':
        return G.set_text(text, node(), str(op['text'])), {}
    if kind == 'font_size':
        return G.set_font_size(text, node(), float(op['size'])), {}
    if kind == 'color':
        return G.set_text_color(text, node(), _rgba(op['color'])), {}
    if kind == 'texture':
        return G.set_texture(text, node(), str(op['texture'])), {}
    if kind == 'show':
        return G.set_node_attrs(text, node(), {'visible': '1' if op.get('visible', True) else '0'}), {}
    if kind == 'attrs':
        vals = op.get('attrs') or {}
        if not all(re.fullmatch(r'[\w:.-]+', k) and '"' not in str(v) for k, v in vals.items()):
            raise StudioError('attrs: 이름은 영문/숫자, 값에 " 금지')
        return G.set_node_attrs(text, node(), vals), {}
    if kind == 'remove':
        n = node()
        return G.remove_node(text, n), {'removed': G.node_label(n)}
    if kind == 'add':
        k = op.get('kind', 'image')
        if k not in ('image', 'button', 'extended_text'):
            raise StudioError('add: kind는 image, button, extended_text')
        parent = node('parent') if 'parent' in op else None
        if parent is None:
            raise StudioError('add: 새 요소를 넣을 부모 노드 번호 "parent"가 필요합니다 (보통 창 노드)')
        tex = op.get('texture')
        label = op.get('text') if k != 'image' else None
        if k == 'extended_text' and label is None:
            label = '새 글자'
        lines = G.new_node(k, int(op.get('x', 0)), int(op.get('y', 0)), int(op.get('w', 100)), int(op.get('h', 40)),
                           tex, label, int(op.get('size', 14)))
        new_text = G.insert_node(text, parent, lines)
        pi = int(op['parent'])
        new_nodes = G.all_nodes(G.parse(new_text))
        # the new node is the last descendant of the parent
        par = new_nodes[pi]
        desc = [i for i, n in enumerate(new_nodes) if G.is_ancestor(par, n)]
        return new_text, {'added_index': max(desc) if desc else None}
    raise StudioError(f'모르는 op {kind!r}: move, move_by, resize, text, font_size, color, texture, show, attrs, '
                      'remove, add')


@tool('gui_edit', 'gui',
      'Edit a GUI layout with a list of ops, applied in order and saved to the mod (only the touched numbers/text '
      'change; the file keeps its format). Indices = gui_tree indices for the layout as it is before each op '
      '(add/remove shift later indices). Ops: {"op":"move","index":i,"x":..,"y":..} (local pos), '
      '{"op":"move_by","index":i,"dx":..,"dy":..}, {"op":"resize","index":i,"w":..,"h":..}, '
      '{"op":"text","index":i,"text":".."}, {"op":"font_size","index":i,"size":14}, '
      '{"op":"color","index":i,"color":"#RRGGBB[AA]"}, {"op":"texture","index":i,"texture":"path under gui_skin"}, '
      '{"op":"show","index":i,"visible":false}, {"op":"attrs","index":i,"attrs":{..}}, {"op":"remove","index":i}, '
      '{"op":"add","parent":i,"kind":"image|button|extended_text","x":..,"y":..,"w":..,"h":..,"texture":..,'
      '"text":..,"size":..}.', writes=True,
      name=STR('Layout.'), ops=ARR('Edits.', {'type': 'object'}), screen=SCREEN,
      dry_run=BOOL('Do not save; just report and return the resulting XML size.', False))
def gui_edit(ctx, name, ops, screen, dry_run):
    name, text, info = _load(ctx, name)
    if not dry_run:
        ctx.st.need_project()
    results = []
    for i, op in enumerate(ops):
        try:
            text, r = apply_op(text, op, _screen(screen))
        except (KeyError, ValueError) as ex:
            raise StudioError(f'op {i} {op}: {ex}')
        results.append({'op': op.get('op'), **r})
    from ..svc_resources import check_xml
    err = check_xml(text)
    if err:
        raise StudioError(f'편집 결과 XML이 깨졌습니다: {err}')
    if not dry_run:
        ctx.st.write(name, textcodec.encode(text, info, errors='xmlcharrefreplace'))
        ctx.notify([name])
    return {'name': name, 'saved': not dry_run, 'results': results, 'nodes': len(G.all_nodes(G.parse(text)))}


@tool('gui_revert', 'gui', 'Drop this mod\'s version of a GUI layout.', writes=True, name=STR('Layout.'))
def gui_revert(ctx, name):
    name, _, _ = _load(ctx, name)
    ok = ctx.st.revert(name)
    ctx.notify([name])
    return {'name': name, 'removed': ok}


@tool('gui_textures', 'gui',
      'gui_skin images usable as node textures (path as written in layouts) with their pixel size.',
      query=STR('Text in the path.', ''), limit=INT('Max rows.', 100))
def gui_textures(ctx, query, limit):
    st = ctx.st
    q = query.lower()
    names = {k: loc.entry.name for k, loc in st.need_game().index.items() if k.startswith('gui_skin\\')}
    for k, (n, _) in st.project_files().items():
        if k.startswith('gui_skin\\'):
            names[k] = n
    out = []
    for k, n in sorted(names.items()):
        if q and q not in k or not k.endswith(('.png', '.tga', '.dds', '.jpg', '.bmp')):
            continue
        row = {'texture': n.split('\\', 1)[1], 'entry': n}
        if len(out) < 40:
            try:
                row['size'] = list(imaging.open_image(st.read(n)).size)
            except Exception:
                pass
        out.append(row)
        if len(out) >= limit:
            break
    return out

