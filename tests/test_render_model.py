"""Offscreen render of models with the native glTF renderer.

    python tests/test_render_model.py <game dir> <out dir>
"""
import math
import os
import sys

GAME, OUT = sys.argv[1], os.path.abspath(sys.argv[2])
os.makedirs(OUT, exist_ok=True)
os.environ['APPDATA'] = os.path.join(OUT, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(OUT, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moderngl  # noqa: E402
from PIL import Image  # noqa: E402

from studio.core import Studio  # noqa: E402
from studio.gl.gltfview import GltfModel, ModelRenderer, frame_for  # noqa: E402
from studio.gl.mathx import OrbitCamera  # noqa: E402
from studio.svc_models import ModelService  # noqa: E402

st = Studio()
st.open_game(GAME)
ms = ModelService(st)
ctx = moderngl.create_standalone_context()
W, H = 480, 480
fbo = ctx.framebuffer(ctx.renderbuffer((W, H), samples=0), ctx.depth_renderbuffer((W, H)))
fbo.use()
ren = ModelRenderer(ctx)
cases = [
    (['Item\\Look\\f1_look_abbear01.nwmt'], ['Character\\F1\\dance_01.nwat'], 1.2),
    (['Npc\\bear01\\bear_01.nwmt'], ['Npc\\bear01\\bear_attack01.nwat'], 0.4),
    (['Object\\obj_chair001.nwmt'], [], 0.0),
]
tiles = []
for names, anims, t in cases:
    info = ms.info(names)
    path = ms.glb(names, anims, info['skeleton'])
    model = GltfModel(path)
    anim = model.animations[0] if model.animations else None
    model.update(anim, t)
    ren.set_model(model)
    lo, hi, r = frame_for(model)
    cam = OrbitCamera()
    cam.frame(lo, hi)
    cam.yaw, cam.pitch = math.radians(30), math.radians(15)
    fbo.clear(0.11, 0.12, 0.15, 1.0)
    ren.render(cam.proj(W / H) @ cam.view(), grid_y=float(lo[1]), grid_scale=max(r / 8, 0.1))
    img = Image.frombytes('RGB', (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
    tiles.append(img)
    print(names[0], ren.stats(), 'anims', len(model.animations),
          f'dur {anim.duration:.2f}s' if anim else '', 'bounds', lo.round(2), hi.round(2))
sheet = Image.new('RGB', (W * len(tiles), H))
for i, im in enumerate(tiles):
    sheet.paste(im, (i * W, 0))
sheet.save(os.path.join(OUT, 'models.png'))
print('saved', os.path.join(OUT, 'models.png'))
