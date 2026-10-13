"""Image helpers (Pillow): previews, format-preserving replacement, colour adjustment, atlas tiles."""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tga', '.bmp', '.gif', '.dds'}
WEB_NATIVE = {'.png', '.jpg', '.jpeg', '.gif'}
_PIL_FORMAT = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.tga': 'TGA', '.bmp': 'BMP', '.gif': 'GIF'}

ATLAS_TILE = 128
ATLAS_GRID = 16


def open_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def info(data: bytes) -> dict:
    im = open_image(data)
    return {'width': im.width, 'height': im.height, 'mode': im.mode, 'format': im.format}


def to_png(data: bytes) -> bytes:
    im = open_image(data)
    if im.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
        im = im.convert('RGBA')
    out = io.BytesIO()
    im.save(out, 'PNG', compress_level=1)
    return out.getvalue()


def encode_like(im: Image.Image, ext: str, like: bytes | None = None) -> bytes:
    """Save im in the format given by ext, keeping alpha when the format supports it."""
    ext = ext.lower()
    fmt = _PIL_FORMAT.get(ext)
    if fmt is None:
        raise ValueError(f'이 형식으로는 저장할 수 없습니다: {ext}')
    ref_mode = None
    if like is not None:
        try:
            ref_mode = open_image(like).mode
        except Exception:
            ref_mode = None
    out = io.BytesIO()
    if fmt == 'JPEG':
        im.convert('RGB').save(out, 'JPEG', quality=95, subsampling=0)
    elif fmt == 'BMP':
        im.convert('RGBA' if ref_mode == 'RGBA' else 'RGB').save(out, 'BMP')
    elif fmt == 'TGA':
        has_alpha = ref_mode in ('RGBA', 'LA') or (ref_mode is None and 'A' in im.getbands())
        im.convert('RGBA' if has_alpha else 'RGB').save(out, 'TGA')
    elif fmt == 'GIF':
        im.save(out, 'GIF')
    else:
        if im.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
            im = im.convert('RGBA')
        im.save(out, 'PNG', optimize=True)
    return out.getvalue()


def convert_upload(upload: bytes, target_ext: str, original: bytes | None = None,
                   fit_size: tuple[int, int] | None = None) -> tuple[bytes, dict]:
    """Convert an uploaded image to the target format. Returns (bytes, notes)."""
    im = open_image(upload)
    notes = {'uploaded_size': [im.width, im.height]}
    if fit_size and (im.width, im.height) != tuple(fit_size):
        im = im.convert('RGBA').resize(tuple(fit_size), Image.LANCZOS)
        notes['resized_to'] = list(fit_size)
    if original is not None and fit_size is None:
        try:
            o = open_image(original)
            if (o.width, o.height) != (im.width, im.height):
                notes['size_changed'] = {'from': [o.width, o.height], 'to': [im.width, im.height]}
        except Exception:
            pass
    return encode_like(im, target_ext, original), notes


def adjust(im: Image.Image, hue: float = 0, saturation: float = 1, brightness: float = 1,
           contrast: float = 1, tint: str | None = None, tint_amount: float = 0) -> Image.Image:
    """hue in degrees (-180..180); others are multipliers. Alpha is preserved."""
    im = im.convert('RGBA')
    alpha = im.getchannel('A')
    rgb = im.convert('RGB')
    if hue:
        h, s, v = rgb.convert('HSV').split()
        shift = int(round(hue / 360 * 255)) % 256
        h = h.point(lambda x: (x + shift) % 256)
        rgb = Image.merge('HSV', (h, s, v)).convert('RGB')
    if saturation != 1:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if brightness != 1:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if contrast != 1:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if tint and tint_amount > 0:
        color = Image.new('RGB', rgb.size, tint)
        rgb = Image.blend(rgb, color, max(0.0, min(1.0, tint_amount)))
    out = rgb.convert('RGBA')
    out.putalpha(alpha)
    return out


def atlas_box(index: int, tile: int = ATLAS_TILE) -> tuple[int, int, int, int]:
    x, y = (index % ATLAS_GRID) * tile, (index // ATLAS_GRID) * tile
    return x, y, x + tile, y + tile


def atlas_tile_size(atlas: Image.Image) -> int:
    return atlas.width // ATLAS_GRID


def get_tile(atlas_bytes: bytes, index: int) -> Image.Image:
    atlas = open_image(atlas_bytes).convert('RGBA')
    return atlas.crop(atlas_box(index, atlas_tile_size(atlas)))


def set_tile(atlas_bytes: bytes, index: int, tile: Image.Image) -> bytes:
    atlas = open_image(atlas_bytes).convert('RGBA')
    size = atlas_tile_size(atlas)
    tile = tile.convert('RGBA')
    if tile.size != (size, size):
        tile = tile.resize((size, size), Image.LANCZOS)
    box = atlas_box(index, size)
    atlas.paste(tile, box[:2])
    out = io.BytesIO()
    atlas.save(out, 'PNG', optimize=True)
    return out.getvalue()


def average_color(im: Image.Image) -> str:
    small = im.convert('RGBA').resize((1, 1), Image.BOX)
    r, g, b, a = small.getpixel((0, 0))
    return f'#{r:02x}{g:02x}{b:02x}'
