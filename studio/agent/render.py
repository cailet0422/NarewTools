"""Pictures for agents: world top / 3D views, GUI layout previews, model views, sound waveforms.

Every picture is drawn from the agent's in-memory state (unsaved edits included) and saved as a
PNG under %APPDATA%\\NarewStudio\\agent\\out; MCP clients also get it inline.
"""
from __future__ import annotations

import io
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from narew import brick

from .. import guilayout as G, imaging, svc_blocks as B, tables
from ..core import StudioError

SKY = (0.61, 0.78, 0.92)
EMPTY = (22, 24, 30)
PRODUCT_RGB = (255, 150, 40)
TREE_RGB = (80, 205, 80)
NEW_RGB = (255, 110, 200)
START_RGB = (255, 60, 60)
MARK_RGB = (60, 230, 255)


def font(size: int = 12):
    for name in ('malgun.ttf', 'malgunbd.ttf', 'arial.ttf', 'segoeui.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# --------------------------------------------------------------------------- brick tables

def _live_sig(ctx) -> tuple:
    g = ctx.st.game
    if not g:
        return ()
    out = []
    for stem, p in sorted(g.live_paths.items()):
        try:
            s = os.stat(p)
            out.append((stem, s.st_size, s.st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


def brick_info(ctx, live: bool) -> dict:
    """Bricks, atlas and per-id colours.  live=True (game saves) adds what the applied mods add,
    as the world editor does for saves."""
    w = ctx.world
    sig = (live, repr(w.mirror.signature(tables.BRICK_TABLE)), repr(w.mirror.signature(tables.ATLAS['diffuse'])),
           repr(ctx.project_root()), _live_sig(ctx) if live else ())

    slot = ('bricks', live)                    # one entry per kind: the atlas is big
    hit = ctx._cache.get(slot)
    if hit and hit[0] == sig:
        return hit[1]
    if live:
        bricks, atlas = w.save_bricks(), w.save_atlas().convert('RGBA')
    else:
        bricks, atlas = B.bricks(ctx.st), B.atlas_image(ctx.st)
    info = {'bricks': bricks, 'by_id': {b['id']: b for b in bricks}, 'atlas': atlas,
            'colors': brick_colors(bricks, atlas)}
    ctx._cache[slot] = (sig, info)
    return info


def brick_colors(bricks: list[dict], atlas: Image.Image) -> np.ndarray:
    """(256, 3) float RGB: average of the top tile tinted by the table colour (like the minimap)."""
    small = np.asarray(atlas.convert('RGB').resize((16, 16), Image.BOX), dtype=np.float32)
    out = np.tile(np.array([255, 60, 255], np.float32), (256, 1))
    out[0] = EMPTY
    out[255] = (200, 120, 220)
    for b in bricks:
        i = b['id']
        if not 0 < i < 255:
            continue
        t = b['tiles'].get('top', -1)
        base = small[t // 16, t % 16] if 0 <= t < 256 else np.array([200, 200, 200], np.float32)
        try:
            argb = int(b.get('color', '0xFFFFFFFF'), 16)
        except ValueError:
            argb = 0xFFFFFFFF
        tint = np.array([(argb >> 16) & 255, (argb >> 8) & 255, argb & 255], np.float32) / 255
        out[i] = base * tint
    return out


def brick_name(info: dict, v: int) -> str:
    if v == 0:
        return 'air'
    if v == 255:
        return 'collision filler (under furniture)'
    b = info['by_id'].get(v)
    return (b['label'] or b['name']) if b else 'unknown'


# --------------------------------------------------------------------------- top view

def _step(scale: int) -> int:
    for s in (1, 2, 5, 10, 20, 50, 100, 200):
        if s * scale >= 28:
            return s
    return 256


def top_view(sess, info, x0, z0, x1, z1, y0=0, y1=None, objects=True, marks=(), max_px=900):
    """Top-down map of an inclusive x/z range; only bricks with y0 <= y <= y1 count, so a low y1
    gives a floor plan.  Returns (image, legend rows)."""
    y1 = sess.H - 1 if y1 is None else y1
    box = sess.clip(x0, y0, z0, x1, y1, z1)
    if box is None:
        raise StudioError('범위가 월드 밖입니다')
    x0, y0, z0, x1, y1, z1 = box
    w, d = x1 - x0 + 1, z1 - z0 + 1
    if w * d > 1024 * 1024:
        raise StudioError('위에서 본 그림은 1024×1024칸까지입니다')
    arr = sess.read_box(*box)
    solid = (arr != 0) & (arr != brick.COLLISION)
    has = solid.any(axis=0)
    top = arr.shape[0] - 1 - np.argmax(solid[::-1], axis=0)
    ids = np.take_along_axis(arr, top[None], 0)[0]
    ty = (top + y0).astype(np.float32)
    rgb = info['colors'][ids].copy()
    if has.any():
        lo, hi = float(ty[has].min()), float(ty[has].max())
        shade = 0.62 + 0.38 * (ty - lo) / max(1.0, hi - lo)
        ddx = np.zeros_like(ty)
        ddz = np.zeros_like(ty)
        ddx[:, 1:] = ty[:, 1:] - ty[:, :-1]
        ddz[1:] = ty[1:] - ty[:-1]
        shade += 0.07 * np.clip(ddx + ddz, -2, 2)
        rgb *= shade[..., None]
    rgb[~has] = EMPTY
    scale = int(max(1, min(24, max_px // max(w, d))))
    big = np.repeat(np.repeat(np.clip(rgb, 0, 255).astype(np.uint8), scale, 0), scale, 1)
    L, T = 46, 34
    img = Image.new('RGB', (L + w * scale + 12, T + d * scale + 12), (14, 15, 19))
    img.paste(Image.fromarray(big), (L, T))
    dr = ImageDraw.Draw(img, 'RGBA')
    f = font(11)
    if scale >= 6:
        for i in range(w + 1):
            X = L + i * scale
            dr.line([(X, T), (X, T + d * scale)], fill=(0, 0, 0, 40))
        for j in range(d + 1):
            Y = T + j * scale
            dr.line([(L, Y), (L + w * scale, Y)], fill=(0, 0, 0, 40))
    step = _step(scale)
    for x in range(x0 - x0 % step, x1 + 1, step):
        if x < x0:
            continue
        X = L + (x - x0) * scale
        dr.line([(X, T - 4), (X, T + d * scale)], fill=(255, 255, 255, 45))
        dr.text((X + 2, T - 16), str(x), font=f, fill=(220, 220, 220))
    for z in range(z0 - z0 % step, z1 + 1, step):
        if z < z0:
            continue
        Y = T + (z - z0) * scale
        dr.line([(L - 4, Y), (L + w * scale, Y)], fill=(255, 255, 255, 45))
        dr.text((2, Y + 1), str(z), font=f, fill=(220, 220, 220))
    dr.text((L, 2), f'x {x0}..{x1} →   z {z0}..{z1} ↓   y {y0}..{y1}', font=f, fill=(255, 255, 255))

    def cell_rect(x, z):
        return (L + (x - x0) * scale, T + (z - z0) * scale, L + (x - x0 + 1) * scale - 1, T + (z - z0 + 1) * scale - 1)

    if objects:
        for key, kind, t, ox, oy, oz, rot in sess.objects_in(x0, y0, z0, x1, y1, z1):
            if kind == brick.OBJ_PRODUCT:
                col = NEW_RGB if key[0] == 'new' else PRODUCT_RGB
                cells = [(ox + a, oz + c) for a, _, c in sess.doc.footprint(t, rot)]
                cx = [min(x1, max(x0, c[0])) for c in cells]
                cz = [min(z1, max(z0, c[1])) for c in cells]
                r0, r1 = cell_rect(min(cx), min(cz)), cell_rect(max(cx), max(cz))
                dr.rectangle((r0[0], r0[1], r1[2], r1[3]), outline=col + (255,), width=2 if scale >= 4 else 1)
                a = cell_rect(ox, oz)
                dr.ellipse((a[0] + scale * 0.25, a[1] + scale * 0.25, a[2] - scale * 0.25 + 1, a[3] - scale * 0.25 + 1),
                           fill=col + (255,))
                if scale >= 12:
                    dr.text((r0[0] + 2, r0[1] + 1), f'#{t}', font=font(10), fill=(0, 0, 0))
            else:
                a = cell_rect(ox, oz)
                dr.ellipse(a, outline=TREE_RGB + (255,), width=2)
        sp = sess.start_pos if sess.doc.is_world else None
        if sp and x0 <= sp[0] < x1 + 1 and z0 <= sp[2] < z1 + 1:
            X, Y = L + (sp[0] - x0) * scale, T + (sp[2] - z0) * scale
            r = max(4, scale * 0.6)
            dr.ellipse((X - r, Y - r, X + r, Y + r), outline=START_RGB + (255,), width=3)
            dr.text((X + r + 1, Y - r), 'start', font=f, fill=START_RGB)
    for m in marks:
        mx, mz = int(m[0]), int(m[-1])
        if x0 <= mx <= x1 and z0 <= mz <= z1:
            a = cell_rect(mx, mz)
            dr.line((a[0], a[1], a[2], a[3]), fill=MARK_RGB + (255,), width=2)
            dr.line((a[0], a[3], a[2], a[1]), fill=MARK_RGB + (255,), width=2)
    # legend: the most common top bricks
    vals, counts = np.unique(ids[has], return_counts=True)
    legend = [{'id': int(v), 'name': brick_name(info, int(v)), 'columns': int(c),
               'rgb': [int(x) for x in info['colors'][int(v)]]}
              for v, c in sorted(zip(vals, counts), key=lambda t: -t[1])[:16]]
    return img, legend, {'box': [x0, y0, z0, x1, y1, z1], 'scale_px_per_brick': scale,
                         'pixel_of_brick': f'brick (x, z) is at pixel ({L}+(x-{x0})*{scale}, {T}+(z-{z0})*{scale})'}


# --------------------------------------------------------------------------- 3D view

def _project(mvp, p, W, H):
    v = mvp @ np.array([p[0], p[1], p[2], 1.0])
    if v[3] <= 1e-6:
        return None
    return ((v[0] / v[3] + 1) * W / 2, (1 - v[1] / v[3]) * H / 2)


def iso_view(ctx, sess, info, box, yaw=35.0, pitch=35.0, size=(960, 640), objects=True, highlight=None,
             context: int = 0):
    """Offscreen 3D picture of the bricks in box (+ context bricks around it)."""
    from ..gl.mathx import OrbitCamera
    from ..gl.voxels import BrickLUT, VoxelRenderer, VoxelStore, game_box_to_render, mesh_region
    x0, y0, z0, x1, y1, z1 = box
    cx0, cz0 = max(0, x0 - context), max(0, z0 - context)
    cx1, cz1 = min(sess.W - 1, x1 + context), min(sess.D - 1, z1 + context)
    if (cx1 - cx0 + 1) * (cz1 - cz0 + 1) > 512 * 512:
        raise StudioError('3D 그림은 512×512칸까지입니다. 범위를 줄이거나 view="top"을 쓰세요')
    arr = sess.read_box(cx0, y0, cz0, cx1, y1, cz1)
    solid = (arr != 0) & (arr != brick.COLLISION)
    if not solid.any():
        raise StudioError('이 범위에는 보이는 블록이 없습니다')
    ys = np.nonzero(solid.any(axis=(1, 2)))[0]
    fy0, fy1 = int(ys[0]) + y0, int(ys[-1]) + y0
    gl = ctx.gl()
    ren = ctx.cached('voxel_renderer', lambda: VoxelRenderer(gl))
    if 'lut' not in info:
        info['lut'] = BrickLUT(info['bricks'])
    lut = info['lut']
    if getattr(ren, '_atlas_of', None) is not info:
        ren.set_atlas(info['atlas'])
        ren._atlas_of = info
    store = VoxelStore(sess.W, sess.D, sess.H)
    for rz in range(cz0 >> 4, (cz1 >> 4) + 1):
        for rx in range(cx0 >> 4, (cx1 >> 4) + 1):
            src = sess.peek(rx, rz)
            a = np.zeros_like(src)
            lx0, lx1 = max(cx0, rx * 16) - rx * 16, min(cx1, rx * 16 + 15) - rx * 16
            lz0, lz1 = max(cz0, rz * 16) - rz * 16, min(cz1, rz * 16 + 15) - rz * 16
            a[y0:y1 + 1, lz0:lz1 + 1, lx0:lx1 + 1] = src[y0:y1 + 1, lz0:lz1 + 1, lx0:lx1 + 1]
            store.put((rx, rz), a)
    try:
        for key, a in store.regions.items():
            ren.upload(key, mesh_region(a, store.neighbours(key), key[0] * 16, key[1] * 16, lut))
        boxes, inst = [], []
        if objects:
            for key, kind, t, ox, oy, oz, rot in sess.objects_in(cx0, y0, cz0, cx1, y1, cz1):
                col = (NEW_RGB if key[0] == 'new' else PRODUCT_RGB) if kind == brick.OBJ_PRODUCT else TREE_RGB
                inst.append([ox + 0.5, oy + 0.5, -(oz + 0.5), *(c / 255 for c in col)])
                if kind == brick.OBJ_PRODUCT:
                    cells = [(ox + a, oy + b, oz + c) for a, b, c in sess.doc.footprint(t, rot)]
                    lo = [min(c[i] for c in cells) for i in range(3)]
                    hi = [max(c[i] for c in cells) for i in range(3)]
                    boxes.append((*game_box_to_render(*lo, *hi), tuple(c / 255 for c in col) + (1.0,)))
        ren.set_objects(np.array(inst, np.float32).reshape(-1, 6))
        if highlight:
            hb = highlight
            boxes.append((*game_box_to_render(*hb), (1.0, 1.0, 0.2, 1.0)))
        cam = OrbitCamera()
        cam.frame((x0, fy0, -(z1 + 1)), (x1 + 1, fy1 + 1, -z0))
        cam.yaw, cam.pitch = math.radians(yaw), math.radians(pitch)
        W, H = size[0] * 2, size[1] * 2
        fbo = gl.simple_framebuffer((W, H))
        try:
            fbo.use()
            fbo.clear(*SKY, 1.0)
            mvp = cam.proj(W / H) @ cam.view()
            ren.render(mvp, SKY, (1e6, 2e6), True, boxes)
            img = Image.frombytes('RGB', (W, H), fbo.read(components=3)).transpose(Image.FLIP_TOP_BOTTOM)
        finally:
            fbo.release()
        img = img.resize(size, Image.LANCZOS)
    finally:
        for key in list(ren.meshes):
            ren.remove(key)
        ren.set_objects(np.zeros((0, 6), np.float32))
    # axis gizmo: which screen direction +x and +z (game) point to
    dr = ImageDraw.Draw(img, 'RGBA')
    f = font(13)
    c = cam.target
    p0 = _project(mvp, c, *size)
    if p0:
        ox, oy = 46, size[1] - 46
        dr.ellipse((ox - 3, oy - 3, ox + 3, oy + 3), fill=(0, 0, 0, 200))
        for label, dv, col in (('+x', (1, 0, 0), (220, 40, 40)), ('+z', (0, 0, -1), (40, 90, 230)),
                               ('+y', (0, 1, 0), (30, 150, 30))):
            p1 = _project(mvp, c + np.array(dv) * cam.dist * 0.08, *size)
            if not p1:
                continue
            vx, vy = p1[0] - p0[0], p1[1] - p0[1]
            n = math.hypot(vx, vy) or 1
            ex, ey = ox + vx / n * 30, oy + vy / n * 30
            dr.line((ox, oy, ex, ey), fill=col + (255,), width=3)
            dr.text((ex + 2, ey - 8), label, font=f, fill=col + (255,))
    dr.text((8, 6), f'3D  x {x0}..{x1}  z {z0}..{z1}  y {fy0}..{fy1}  yaw {yaw:g}° pitch {pitch:g}°', font=f,
            fill=(20, 20, 30, 255))
    return img, {'content_y': [fy0, fy1]}


# --------------------------------------------------------------------------- GUI layouts

TEXT_TYPES = ('extended_text', 'text', 'button', 'editbox')


class GuiPainter:
    """Draws a Gui\\*.xml layout with its gui_skin images (PIL port of the GUI editor's painter)."""

    def __init__(self, st):
        self.st = st
        self._img: dict = {}
        self._fonts: dict = {}
        self._names = None

    def texture_name(self, path: str) -> str | None:
        entry = G.texture_entry(path)
        if self.st.exists(entry):
            return self.st.canonical_name(entry)
        if self._names is None:
            names = [loc.entry.name for loc in self.st.game.index.values()]
            names += [n for n, _ in self.st.project_files().values()]
            self._names = tables.BasenameIndex(names)
        return self._names.find(path, prefer_top=G.SKIN_DIR)

    def image(self, path: str):
        key = path.strip().lower()
        if key not in self._img:
            name = self.texture_name(path)
            try:
                self._img[key] = imaging.open_image(self.st.read(name)).convert('RGBA') if name else None
            except Exception:
                self._img[key] = None
        return self._img[key]

    def font(self, file: str | None, px: int):
        file = (file or '').strip()
        k = (file, px)
        if k not in self._fonts:
            f = None
            name = self.texture_name(file) if file else None
            if name:
                try:
                    f = ImageFont.truetype(io.BytesIO(self.st.read(name)), px)
                except Exception:
                    f = None
            self._fonts[k] = f or font(px)
        return self._fonts[k]

    @staticmethod
    def _nine(canvas, im, rect, a):
        x, y, W, H = rect
        sw = min(im.width, int(G._num(a.get('width'), im.width)))
        sh = min(im.height, int(G._num(a.get('height'), im.height)))
        l, r = G._num(a.get('left_x')), G._num(a.get('right_x'))
        t, b = G._num(a.get('top_y')), G._num(a.get('bottom_y'))
        kx = min(1.0, W / (l + r)) if l + r > 0 else 1.0
        ky = min(1.0, H / (t + b)) if t + b > 0 else 1.0
        sx = [0, l, sw - r, sw]
        sy = [0, t, sh - b, sh]
        dx = [0, l * kx, W - r * kx, W]
        dy = [0, t * ky, H - b * ky, H]
        for j in range(3):
            for i in range(3):
                sbox = (int(sx[i]), int(sy[j]), int(sx[i + 1]), int(sy[j + 1]))
                dw, dh = int(round(dx[i + 1] - dx[i])), int(round(dy[j + 1] - dy[j]))
                if sbox[2] > sbox[0] and sbox[3] > sbox[1] and dw > 0 and dh > 0:
                    piece = im.crop(sbox).resize((dw, dh), Image.BILINEAR)
                    canvas.alpha_composite(piece, (int(round(x + dx[i])), int(round(y + dy[j]))))

    def render(self, root, screen=(1920, 1080), show_hidden=False, labels=True, highlight=(), crop='window'):
        w, h = screen
        boxes = G.layout(root, w, h, lambda p: (lambda im: im.size if im else None)(self.image(p)) if p else None)
        canvas = Image.new('RGBA', (w, h), (27, 31, 39, 255))
        drawn = []
        for i, b in enumerate(boxes):
            if not b.visible and not show_hidden:
                continue
            n = b.node
            x, y, bw, bh = int(round(b.x)), int(round(b.y)), int(round(b.w)), int(round(b.h))
            if bw <= 0 or bh <= 0 or x > w or y > h or x + bw < 0 or y + bh < 0:
                continue
            drawn.append(i)
            tex = n.child('texture')
            if tex is not None and tex.text:
                im = self.image(tex.text)
                if im is not None:
                    ss = n.child('skinset')
                    if ss is not None:
                        self._nine(canvas, im, (x, y, bw, bh), ss.attrs)
                    elif tex.attrs.get('uv', '').strip() == 'wrap':
                        for ty in range(y, y + bh, im.height):
                            for tx in range(x, x + bw, im.width):
                                if tx >= 0 and ty >= 0:
                                    piece = im.crop((0, 0, min(im.width, x + bw - tx), min(im.height, y + bh - ty)))
                                    canvas.alpha_composite(piece, (tx, ty))
                    elif x >= 0 and y >= 0:
                        canvas.alpha_composite(im.resize((bw, bh), Image.BILINEAR), (x, y))
                    else:
                        layer = Image.new('RGBA', canvas.size)
                        layer.paste(im.resize((bw, bh), Image.BILINEAR), (x, y))
                        canvas.alpha_composite(layer)
            txt = n.child('text')
            if n.attrs.get('type', '').strip() in TEXT_TYPES and txt is not None and txt.text:
                fn = n.child('font')
                size = G._num(fn.attrs.get('size'), 14) if fn is not None else 14
                f = self.font(fn.text if fn is not None else None, max(6, int(size * 1.33)))
                colr = G.argb(n.child('color').attrs.get('font') if n.child('color') is not None else None) \
                    or (255, 255, 255, 255)
                text = G.node_text(n) or ''
                dr = ImageDraw.Draw(canvas)
                tb = dr.multiline_textbbox((0, 0), text, font=f)
                tw, th = tb[2] - tb[0], tb[3] - tb[1]
                ax = txt.attrs.get('alignx', 'left').strip()
                ay = txt.attrs.get('aligny', 'top').strip()
                tx = x + (bw - tw if ax == 'right' else (bw - tw) / 2 if ax == 'center' else 0)
                ty = y + (bh - th if ay == 'bottom' else (bh - th) / 2 if ay == 'center' else 0)
                dr.multiline_text((tx - tb[0], ty - tb[1]), text, font=f,
                                  fill=(colr[0], colr[1], colr[2], colr[3] or 255))
        dr = ImageDraw.Draw(canvas, 'RGBA')
        lf = font(12)
        for i in drawn:
            b = boxes[i]
            r = (b.x, b.y, b.x + b.w - 1, b.y + b.h - 1)
            if i in highlight:
                dr.rectangle(r, outline=(255, 225, 74, 255), width=3)
            elif labels:
                dr.rectangle(r, outline=(120, 140, 170, 90), width=1)
        if labels:
            for i in drawn:
                b = boxes[i]
                t = str(i)
                tb = dr.textbbox((0, 0), t, font=lf)
                X, Y = max(0, b.x), max(0, b.y)
                dr.rectangle((X, Y, X + tb[2] - tb[0] + 4, Y + tb[3] - tb[1] + 4),
                             fill=(255, 225, 74, 220) if i in highlight else (0, 0, 0, 170))
                dr.text((X + 2 - tb[0], Y + 2 - tb[1]), t, font=lf,
                        fill=(0, 0, 0) if i in highlight else (255, 255, 255))
        box = (0, 0, w, h)
        if crop == 'window':
            rects = [boxes[i] for i in drawn if boxes[i].depth <= 1]
            if rects:
                bx0 = max(0, int(min(b.x for b in rects)) - 20)
                by0 = max(0, int(min(b.y for b in rects)) - 20)
                bx1 = min(w, int(max(b.x + b.w for b in rects)) + 20)
                by1 = min(h, int(max(b.y + b.h for b in rects)) + 20)
                if bx1 > bx0 and by1 > by0:
                    box = (bx0, by0, bx1, by1)
        out = canvas.crop(box).convert('RGB')
        if out.width > 1400:
            out = out.resize((1400, int(out.height * 1400 / out.width)), Image.LANCZOS)
        return out, boxes, {'crop': list(box), 'drawn': len(drawn)}


# --------------------------------------------------------------------------- models / sound

def model_view(ctx, names, anims=(), time_s=0.0, yaw=30.0, pitch=15.0, size=(640, 640)):
    info = ctx.models.info(names)
    path = ctx.models.glb(names, list(anims), info['skeleton'])
    img, meta = glb_view(ctx, path, time_s, yaw, pitch, size)
    meta['skeleton'] = info['skeleton']
    return img, meta


def glb_view(ctx, path, time_s=0.0, yaw=30.0, pitch=15.0, size=(640, 640), time_frac=None):
    """Picture of a .glb (first animation at ``time_s``, or at ``time_frac`` of its length)."""
    from ..gl.gltfview import GltfModel, ModelRenderer, frame_for
    from ..gl.mathx import OrbitCamera
    gl = ctx.gl()
    ren = ctx.cached('model_renderer', lambda: ModelRenderer(gl))
    model = GltfModel(path)
    anim = model.animations[0] if model.animations else None
    if time_frac is not None and anim is not None:
        time_s = float(anim.duration) * float(time_frac)
    model.update(anim, time_s)
    ren.set_model(model)
    lo, hi, r = frame_for(model)
    cam = OrbitCamera()
    cam.frame(lo, hi)
    cam.yaw, cam.pitch = math.radians(yaw), math.radians(pitch)
    W, H = size[0] * 2, size[1] * 2
    fbo = gl.framebuffer(gl.renderbuffer((W, H)), gl.depth_renderbuffer((W, H)))
    try:
        fbo.use()
        fbo.clear(0.11, 0.12, 0.15, 1.0)
        ren.render(cam.proj(W / H) @ cam.view(), grid_y=float(lo[1]), grid_scale=max(r / 8, 0.1))
        img = Image.frombytes('RGB', (W, H), fbo.read(components=3)).transpose(Image.FLIP_TOP_BOTTOM)
    finally:
        fbo.release()
        ren.set_model(None)
    return img.resize(size, Image.LANCZOS), {
        'bounds': [list(map(float, lo)), list(map(float, hi))], 'animations': len(model.animations),
        'duration': float(anim.duration) if anim else 0.0}


def waveform(clip, width=900, height=200, marks=()) -> Image.Image:
    from .. import audio
    img = Image.new('RGB', (width, height), (20, 22, 28))
    dr = ImageDraw.Draw(img)
    ch = clip.channels
    lo, hi = audio.peaks(clip, 0, clip.frames, width)
    band = height // max(1, ch)
    for c in range(ch):
        mid = c * band + band // 2
        dr.line((0, mid, width, mid), fill=(60, 64, 76))
        for x in range(width):
            a, b = mid - hi[c, x] * band / 2, mid - lo[c, x] * band / 2
            dr.line((x, a, x, b), fill=(95, 179, 107))
    f = font(11)
    dur = clip.duration
    for s in range(int(dur) + 1):
        X = s / max(dur, 1e-9) * width
        dr.line((X, height - 8, X, height), fill=(200, 200, 200))
        dr.text((X + 2, height - 20), f'{s}s', font=f, fill=(200, 200, 200))
    for t in marks:
        X = t / max(dur, 1e-9) * width
        dr.line((X, 0, X, height), fill=(255, 200, 60))
    return img


def save(img: Image.Image, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, 'PNG', optimize=False)
    return path
