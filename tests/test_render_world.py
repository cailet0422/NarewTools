"""Offscreen render of a world and a prefab with the native voxel renderer.

    python tests/test_render_world.py <game dir> <out dir>
"""
import math
import os
import sys
import time

GAME, OUT = sys.argv[1], os.path.abspath(sys.argv[2])
os.makedirs(OUT, exist_ok=True)
os.environ['APPDATA'] = os.path.join(OUT, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(OUT, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moderngl  # noqa: E402
from PIL import Image  # noqa: E402

from studio import svc_blocks  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.gl.mathx import OrbitCamera  # noqa: E402
from studio.gl.voxels import BrickLUT, VoxelRenderer, VoxelStore, mesh_region, raycast  # noqa: E402
from studio.svc_world import WorldService  # noqa: E402

st = Studio()
st.open_game(GAME)
ws = WorldService(st)
lut = BrickLUT(svc_blocks.bricks(st))
ctx = moderngl.create_standalone_context()
W, H = 960, 600
fbo = ctx.simple_framebuffer((W, H))
fbo.use()
ren = VoxelRenderer(ctx)
ren.set_atlas(svc_blocks.atlas_image(st))

for name, yawdeg in (('map\\64_0.bwr', 35), ('Architecture\\building\\Choga_big_A01.iaf', 210)):
    doc = ws.open(name)
    store = VoxelStore(doc.width, doc.depth, doc.height)
    t0 = time.time()
    for rz in range(doc.rz):
        for rx in range(doc.rx):
            store.put((rx, rz), doc.region_array(rx, rz))
    t1 = time.time()
    faces = 0
    for key in list(store.regions):
        mesh = mesh_region(store.regions[key], store.neighbours(key), key[0] * 16, key[1] * 16, lut)
        faces += sum(len(v[1]) // 6 for v in mesh.values())
        ren.upload(key, mesh)
    t2 = time.time()
    cam = OrbitCamera()
    ys = [store.top_y(x, z) for x in range(0, doc.width, 4) for z in range(0, doc.depth, 4)]
    top = max(ys) + 1
    cam.frame((0, 0, -doc.depth), (doc.width, top, 0))
    cam.yaw = math.radians(yawdeg)
    cam.pitch = math.radians(28)
    fbo.clear(0.61, 0.78, 0.92, 1.0)
    mvp = cam.proj(W / H) @ cam.view()
    hit = raycast(store, *cam.ray(W / 2, H / 2, W, H))
    boxes = []
    if hit:
        (x, y, z), n = hit
        boxes.append(((x, y, -(z + 1)), (1, 1, 1), (1, 1, 0, 1)))
    ren.render(mvp, boxes=boxes)
    img = Image.frombytes('RGB', (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
    fn = os.path.join(OUT, os.path.basename(name).replace('.', '_') + '.png')
    img.save(fn)
    print(f'{name}: regions {len(store.regions)} load {t1 - t0:.2f}s mesh+upload {t2 - t1:.2f}s faces {faces} '
          f'center hit {hit} -> {fn}')
    if hit:
        (x, y, z), _ = hit
        print('   hit brick', lut.label(store.get(x, y, z)))
    ren.clear()
