"""AI-agent tools end to end on a fake game folder (never the real install).

    python tests/test_agent.py <game dir> <scratch dir>

Copies the game's packs (read-only use) into <scratch>\\game with a fake Local\\ world save, points
APPDATA/LOCALAPPDATA/USERPROFILE at the scratch folder, then drives every tool group through the same
registry the MCP server uses: project, world editing (fill/build/shape/paste/furniture/undo/save, ASCII views,
top + 3D pictures), saving a game save to a new file and applying it back (dry run + confirm), content creation,
tables/strings, GUI edit + render, sound edit, model render, the mod manager (dry run), and the MCP protocol and
CLI in subprocesses.
"""
import json
import os
import shutil
import subprocess
import sys
import time

GAME_SRC, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(SCRATCH)
HOME = os.path.join(SCRATCH, 'home')
os.makedirs(os.path.join(HOME, 'Documents'))
ENV = {'APPDATA': os.path.join(SCRATCH, 'appdata'), 'LOCALAPPDATA': os.path.join(SCRATCH, 'localappdata'),
       'USERPROFILE': HOME}
os.environ.update(ENV)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ------------------------------------------------------------------ fake game folder (packs are hard-linked/copied)
GAME = os.path.join(SCRATCH, 'game')
os.makedirs(os.path.join(GAME, 'data'))
PACKS = ('xml', 'default', 'architecture', 'map', 'gui', 'gui_skin', 'sound', 'object', 'icon', 'font')
for stem in PACKS:                    # what the tested tools read (the studio never writes packs)
    shutil.copy2(os.path.join(GAME_SRC, 'data', stem + '.pack'), os.path.join(GAME, 'data', stem + '.pack'))
shutil.copytree(os.path.join(GAME_SRC, 'Language'), os.path.join(GAME, 'Language'))
for exe in ('IsletClient_release(x64).exe',):
    open(os.path.join(GAME, exe), 'wb').close()

from narew import brick  # noqa: E402
from studio.agent import cli  # noqa: E402
from studio.agent.context import AgentContext  # noqa: E402
from studio.agent.registry import TOOLS, call  # noqa: E402

cli._load_tools()
checks = []


def check(name, ok, info=''):
    checks.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{info}]' if info and not ok else ''), flush=True)


def T(tool, **args):
    ok, data, images = call(ctx, tool, args)
    if not ok:
        print(f'   {tool} -> ERROR {data}')
    return ok, data, images


def must(tool, **args):
    ok, data, images = T(tool, **args)
    check(f'{tool} ok', ok, data if not ok else '')
    return data if ok else {}


ctx = AgentContext()
d = must('set_game', path=GAME)
check('game indexed', d.get('entries', 0) > 5000)
d = must('project_new', name='에이전트 테스트', id='agent-test')
check('project created', d.get('id') == 'agent-test')
st = must('status')
check('status shows project', st['project'] and st['project']['id'] == 'agent-test')

# ------------------------------------------------------------------ catalogs
bl = must('bricks_list', limit=500)['bricks']
ids = {b['id'] for b in bl}
STONE = 60 if 60 in ids else bl[0]['id']
WOOD = next((b['id'] for b in bl if b['category'].lower() == 'wood'), bl[1]['id'])
GLASS = next((b['id'] for b in bl if b['category'].lower() == 'glass'), bl[2]['id'])
pl = must('products_list', placeable_only=True, limit=2000)['products']
check('placeable products listed', len(pl) > 500)
small = [p for p in pl if p['size_xyz'][0] <= 2 and p['size_xyz'][2] <= 2 and p['size_xyz'][1] <= 2]
CHAIR = 1 if any(p['id'] == 1 for p in small) else small[0]['id']
BED = 2 if any(p['id'] == 2 for p in pl) else pl[1]['id']

# ------------------------------------------------------------------ world: open and look
d = must('world_open', name='map\\64_0.bwr')
W = d['session']
check('world opened', d['kind'] == 'world' and d['size_xyz'][1] == 256)
hm = must('world_heightmap', session=W, area=[8, 8, 40, 40])
import numpy as np  # noqa: E402
H = np.array([[int(v) for v in line.split()[1:]] for line in hm.get('heights', '').splitlines()])
check('heightmap has numbers', hm.get('max_y') is not None and H.shape == (33, 33), H.shape)
# find a flat 12x12 spot
spot = None
for z in range(0, 33 - 12):
    for x in range(0, 33 - 12):
        sub = H[z:z + 12, x:x + 12]
        if sub.min() == sub.max() and sub.min() >= 0:
            spot = (x + 8, z + 8, int(sub.min()))
            break
    if spot:
        break
print('   flat spot:', spot, 'heights', H.min(), '..', H.max())
X0, Z0, GY = spot if spot else (16, 16, int(H.max()))      # hilly map: build above the highest point
Y = GY + 1
g = must('world_ground', session=W, x=X0, z=Z0)
check('ground agrees with heightmap', g.get('place_at_y') == H[Z0 - 8][X0 - 8] + 1, (g.get('place_at_y'), H[Z0 - 8][X0 - 8]))

# ------------------------------------------------------------------ world: edit
s = ctx.sessions[W]
r = must('world_fill', session=W, box=[X0, Y, Z0, X0 + 8, Y, Z0 + 6], brick=WOOD)
check('floor filled', r.get('changed') == 9 * 7)
r = must('world_fill', session=W, box=[X0, Y + 1, Z0, X0 + 8, Y + 4, Z0 + 6], brick=STONE, mode='walls')
check('walls = perimeter x 4 layers', r.get('changed') == (2 * 9 + 2 * 5) * 4, r)
r = must('world_fill', session=W, box=[X0 + 4, Y + 1, Z0, X0 + 4, Y + 2, Z0], brick=0)
check('door cut', r.get('changed') == 2 and s.get(X0 + 4, Y + 1, Z0) == 0)
r = must('world_build', session=W, origin=[X0, Y + 5, Z0], legend={'#': GLASS, 'W': WOOD},
         layers=[['WWWWWWWWW'] + ['W#######W'] * 5 + ['WWWWWWWWW']])
check('roof from blueprint', r.get('changed') == 63 and s.get(X0 + 1, Y + 5, Z0 + 1) == GLASS)
r = must('world_set_blocks', session=W, blocks=[[X0 + 2, Y + 2, Z0, GLASS], [X0 + 6, Y + 2, Z0, GLASS]])
check('windows set', r.get('changed') == 2)
r = must('world_place', session=W, product=CHAIR, x=X0 + 2, z=Z0 + 3, dry_run=True)
check('default y = roof, lower floor noted', r['pos'][1] == Y + 6 and str(Y + 1) in r.get('note', ''), r)
r = must('world_place', session=W, product=CHAIR, x=X0 + 2, z=Z0 + 3, y=Y + 1)
check('chair placed on the floor', r.get('ok') and r['pos'][1] == Y + 1, r)
chair_id = r.get('id')
cells = r.get('footprint_cells', [])
check('chair collision written', cells and all(s.get(*c) == brick.COLLISION for c in cells))
r = T('world_place', session=W, product=CHAIR, x=X0 + 2, z=Z0 + 3, y=Y + 1)
check('second chair on the same cell refused', not r[0])
r = must('world_place', session=W, product=BED, x=X0 + 6, z=Z0 + 3, y=Y + 1, dry_run=True)
check('dry run reports', 'ok' in r and 'footprint_cells' in r)
r = must('world_place_many', session=W, items=[{'product': CHAIR, 'x': X0 + 1, 'z': Z0 + 5, 'y': Y + 1},
                                                {'product': CHAIR, 'x': X0 + 4, 'z': Z0 - 50}])
check('place_many: 1 placed, 1 reported', len(r.get('placed', [])) == 1 and len(r.get('failed', [])) == 1, r)
r = must('world_shape', session=W, shape='sphere', at=[X0 + 4, Y + 14, Z0 + 3], radius=3, brick=GLASS, hollow=True)
check('hollow sphere drawn', r.get('changed', 0) > 50 and s.get(X0 + 4, Y + 14, Z0 + 3) == 0)
r = must('world_shape', session=W, shape='line', at=[X0, Y + 10, Z0], to=[X0 + 8, Y + 10, Z0 + 6], brick=STONE)
check('line drawn', r.get('changed') == 9)
r = must('world_undo', session=W, steps=2)
check('undo 2', r.get('undone') == ['line', 'sphere'] and s.get(X0, Y + 10, Z0) != STONE)
r = must('world_redo', session=W)
check('redo sphere', r.get('redone') == ['sphere'])
must('world_undo', session=W)
objs = must('world_objects', session=W, box=[X0, Y, Z0, X0 + 8, Y + 4, Z0 + 6])
check('objects listed', objs.get('count') == 2)

# looks
lay = must('world_layer', session=W, y=Y + 1, area=[X0 - 1, Z0 - 1, X0 + 9, Z0 + 7])
check('layer ascii shows furniture anchor', '&' in lay.get('grid', ''))
print(lay.get('grid'))
sec = must('world_section', session=W, axis='z', at=Z0, start=X0 - 1, end=X0 + 9)
check('section ascii', 'grid' in sec)
print(sec.get('grid'))
ok, d, imgs = T('world_render', session=W, view='top', area=[X0 - 4, Z0 - 4, X0 + 12, Z0 + 10], y_max=Y + 1)
check('top render', ok and imgs and os.path.getsize(imgs[0]) > 1000)
ok, d, imgs = T('world_render', session=W, view='iso', box=[X0 - 2, Y - 2, Z0 - 2, X0 + 10, Y + 8, Z0 + 8])
check('iso render (OpenGL)', ok and imgs and os.path.getsize(imgs[0]) > 5000, d)
RENDERS = list(imgs)

# copy / paste with furniture, prefab
cp = must('world_copy', session=W, box=[X0, Y, Z0, X0 + 8, Y + 5, Z0 + 6])
check('copy has 2 products', cp.get('products') == 2)
r = must('world_paste', session=W, clip=cp.get('clip'), at=[X0 + 14, Y + 20, Z0], rotate=1)
check('paste rotated places furniture again', len(r.get('placed', [])) == 2 and not r.get('failed'), r)
check('rotated size swaps x/z', r.get('box') and r['box'][3] - r['box'][0] == 6 and r['box'][5] - r['box'][2] == 8)
r = must('world_save_prefab', name='Architecture\\building\\agent_house.iaf', clip=cp.get('clip'))
check('prefab saved', r.get('size_xyz') == [16, 6, 16])
r = must('world_delete_objects', session=W, ids=[chair_id])
check('chair deleted, collision cleared', r.get('deleted_objects') and all(s.get(*c) == 0 for c in cells))
must('world_set_start', session=W, x=X0 + 4, z=Z0 + 3)
check('start moved', abs(s.start_pos[0] - (X0 + 4.5)) < 1e-6)
r = must('world_save', session=W)
check('saved into project', r.get('into') == 'mod project')
check('session clean after save', not s.dirty())
# strict re-read with the engine's object rules
data = ctx.st.read('map\\64_0.bwr', 'base')
w2 = brick.World.from_bytes(data, ctx.world.product_table(), strict=True)
n_prod = sum(1 for _, o in w2.iter_objects() if o.kind == brick.OBJ_PRODUCT and o.type_id == CHAIR)
check('saved world re-reads strictly', w2.width == 64)
check('placed chairs in saved file', n_prod >= 3, n_prod)
check('saved brick', w2.get(X0 + 1, Y + 5, Z0 + 1) == GLASS)
check('saved start', abs(w2.tail.start_pos[0] - (X0 + 4.5)) < 1e-4)

# prefab session + paste prefab
d = must('world_open', name='Architecture\\building\\agent_house.iaf')
P = d['session']
check('prefab opens', d['kind'] == 'prefab' and d['size_xyz'] == [16, 6, 16])
r = must('world_paste', session=W, prefab='Architecture\\building\\agent_house.iaf', at=[X0, Y + 40, Z0])
check('prefab pasted', r.get('changed', 0) > 50)
must('world_undo', session=W)
check('unsaved after undo = clean', not s.dirty())

# ------------------------------------------------------------------ the user's own world (fake Local save)
local = os.path.join(GAME, 'Local')
os.makedirs(local)
src = ctx.st.read('map\\64_0.bwr', 'game')
with open(os.path.join(local, 'lj.bwr'), 'wb') as f:
    f.write(src)
for ext in ('.lnd', '.npc', '.db'):
    with open(os.path.join(local, 'lj' + ext), 'wb') as f:
        f.write(b'fake ' + ext.encode())
ls = must('world_list', kind='save')
check('game save listed', any(r['name'] == 'lj.bwr' for r in ls.get('saves', [])))
d = must('world_open', path=os.path.join(local, 'lj.bwr'))
S = d['session']
check('save opened as game save', 'game save' in (d.get('source') or ''))
must('world_fill', session=S, box=[X0, Y, Z0, X0 + 2, Y, Z0 + 2], brick=GLASS)
r = must('world_save', session=S)
newp = r.get('saved', '')
check('game save written to a new file', newp.endswith('lj_edit.bwr') and os.path.isfile(newp))
with open(os.path.join(local, 'lj.bwr'), 'rb') as f:
    check('original save untouched', f.read() == src)
r = T('world_save', session=S, path=os.path.join(local, 'evil.bwr'))
check('saving into Local\\ refused', not r[0])
r = must('world_apply_to_game', session=S)
check('apply: dry run first', r.get('dry_run') and r.get('world') == 'lj.bwr')
r = must('world_apply_to_game', session=S, confirm=True)
check('apply with confirm', r.get('applied'))
with open(os.path.join(local, 'lj.bwr'), 'rb') as f:
    check('Local save replaced', f.read() != src)
b = must('world_saves').get('backups', [])
check('backup made', b and any(x.endswith('.npc') for x in b[0]['files']))
r = must('world_restore_backup', backup=b[0]['dir'], confirm=True)
with open(os.path.join(local, 'lj.bwr'), 'rb') as f:
    check('backup restored', f.read() == src)

# ------------------------------------------------------------------ content
ok, d, imgs = T('texture_make', color='#8fc9a8', pattern='bricks')
TEX = d.get('path') if ok else None
check('texture made', TEX and os.path.isfile(TEX))
r = must('content_new_block', name_kor='에이전트 블록', textures={'all': TEX}, template_brick=STONE,
         recipe={'station': 'hand', 'ingredients': [[STONE, 2]]})
NEWB = r.get('brick')
check('new block', NEWB and r.get('craft'))
bl2 = must('bricks_list', query=str(NEWB))
check('new block in catalog', any(b['id'] == NEWB for b in bl2.get('bricks', [])))
r = must('world_fill', session=W, box=[X0, Y + 30, Z0, X0 + 1, Y + 30, Z0 + 1], brick=NEWB)
check('new block usable in world', r.get('changed') == 4)
must('world_undo', session=W)
r = must('content_new_furniture', name_kor='에이전트 의자', template_product=CHAIR, model_name='agent_chair.nwmt',
         copy_model=next(p['model'] for p in pl if p['id'] == CHAIR), icon=TEX)
NEWP = r.get('product')
check('new furniture', NEWP)
r = must('world_place', session=W, product=NEWP, x=X0 + 3, z=Z0 + 2, y=Y + 1)
check('new furniture placeable at once', r.get('ok'), r)
must('world_undo', session=W)
items = must('items_search', query='', limit=5).get('items', [])
r = must('content_new_item', name_kor='에이전트 물약', template_item=items[0]['id'], icon=TEX)
check('new item', r.get('item'))
added = must('content_added')
check('content_added lists records', added.get('count', 0) >= 6)

# tables / strings / text
r = must('table_records', table='XML\\brick_table.xml', key=str(NEWB))
check('table_records reads mod record', r.get('matches') == 1 and '에이전트' in r['records'][0]['xml'] or
      r.get('matches') == 1)
r = must('string_set', table='item_name', index=NEWB, kor='에이전트 블록2', eng='Agent block 2')
r = must('string_get', table='item_name', index=NEWB)
check('string set/get', r.get('kor') == '에이전트 블록2')
xml = must('table_records', table='XML\\item_table.xml', key=str(r and NEWB))['records'][0]['xml']
r = must('table_upsert', table='XML\\item_table.xml', xml=xml.replace(f'id="{NEWB}"', 'id="19990"'))
check('table_upsert adds', r.get('action') == 'added')
r = must('table_remove', table='XML\\item_table.xml', tag='item', key='19990')
check('table_remove', r.get('removed'))
fs = must('files_search', query='Gui\\inventory', kind='text')
check('files_search', fs.get('total', 0) >= 1)
tr = must('text_read', name='Gui\\sound.xml', grep='click')
check('text_read grep', tr.get('matches', 0) >= 1)

# GUI
tree = must('gui_tree', name='inventory', max_depth=2)
check('gui tree', tree.get('nodes_total', 0) > 5)
idx = next((n['index'] for n in tree.get('nodes', []) if n['depth'] == 1), 1)
x, y = next(n['pos'] for n in tree['nodes'] if n['index'] == idx)
r = must('gui_edit', name='inventory', ops=[{'op': 'move_by', 'index': idx, 'dx': 7, 'dy': -3},
                                            {'op': 'add', 'parent': 0, 'kind': 'extended_text', 'x': 10, 'y': 10,
                                             'w': 200, 'h': 30, 'text': '에이전트'}])
check('gui edit saved', r.get('saved') and r['results'][1].get('added_index') is not None)
tree2 = must('gui_tree', name='inventory')
check('gui moved', next(n['pos'] for n in tree2['nodes'] if n['index'] == idx) == [x + 7, y - 3])
check('gui text added', any(n.get('text') == '에이전트' for n in tree2['nodes']))
ok, d, imgs = T('gui_render', name='inventory', highlight=[idx])
check('gui render', ok and imgs and os.path.getsize(imgs[0]) > 5000, d)
RENDERS += imgs

# sound
snd = must('sound_list', query='.wav', limit=5)['sounds']
if snd:
    nm = snd[0]['name']
    info0 = must('sound_info', name=nm, waveform=False)
    r = must('sound_edit', name=nm, ops=[{'op': 'fade_out', 'start': info0['seconds'] / 2},
                                         {'op': 'gain', 'db': -3}])
    info1 = must('sound_info', name=nm, waveform=False)
    check('sound edited, format kept', r.get('saved') and info1['rate'] == info0['rate']
          and info1['channels'] == info0['channels'] and info1['status'] == 'modified')

# models
ok, d, imgs = T('model_render', names=['Object\\obj_chair001.nwmt'])
check('model render', ok and imgs, d)

# images
ok, d, imgs = T('image_view', name='terrain.png', max_size=256)
check('image_view', ok and imgs)
ok, d, imgs = T('block_tile', index=0)
check('block_tile', ok and imgs)

# mod manager (dry run only; never the real install)
must('manager_install_project')
ms = must('manager_status')
check('installed in manager', any(m['id'] == 'agent-test' for m in ms.get('mods', [])))
from modmanager.core import Manager  # noqa: E402
m = Manager()
m.set_game(GAME)
r = must('manager_apply')
check('manager apply dry run', r.get('dry_run') and r.get('files_by_pack'))

# errors are helpful
ok, d, _ = T('world_fill', session='w99', box=[0, 0, 0, 1, 1, 1], brick=1)
check('unknown session -> message', not ok and 'world_open' in d)
ok, d, _ = T('world_fill', session=W, box=[0, 0, 0], brick=1)
check('bad box -> message', not ok and 'box' in d)
ok, d, _ = T('world_fill', session=W, box=[0, 0, 0, 1, 1, 1], brick=1, color='red')
check('unknown arg -> message', not ok and 'allowed' in d)

# packs (explorer)
r = must('pack_extract', out=os.path.join(SCRATCH, 'extract'), pack='gui', filter='Gui\\inventory*')
check('pack_extract filtered', r.get('files', 0) >= 1 and os.path.isfile(os.path.join(SCRATCH, 'extract', 'gui', 'Gui', 'inventory.xml')))
r = T('pack_extract', out=os.path.join(GAME, 'x'), pack='gui')
check('pack_extract refuses the game folder', not r[0])
r = must('pack_verify', pack='architecture')
check('pack_verify', r.get('ok'))

# output formatting: long text goes into its own block, small lists stay on one line
from studio.agent.mcp import render_result  # noqa: E402
blocks = render_result(lay)
check('ascii grid in its own text block', len(blocks) == 2 and blocks[1].count('\n') > 5 and '\\n' not in blocks[0])
check('small lists inline', '"x": [' in blocks[0] and blocks[0].count('\n') < 40, blocks[0][:300])

# ------------------------------------------------------------------ live link to the window
from studio.agent import live  # noqa: E402
seq = live.last_seq()
check('changes were announced', seq > 5)
_, evs = live.events_after(0)
check('world save announced', any('map\\64_0.bwr' in (e.get('names') or []) for e in evs))

# ------------------------------------------------------------------ MCP + CLI in subprocesses
env = dict(os.environ, **ENV, PYTHONUSERBASE=os.environ.get('PYTHONUSERBASE') or os.path.join(
    os.path.expanduser('~'), 'AppData', 'Roaming', 'Python'))
reqs = [
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
     'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}}},
    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'world_open',
                                                                     'arguments': {'name': 'map\\64_0.bwr'}}},
    {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
        'name': 'world_render', 'arguments': {'session': 'w1', 'view': 'top', 'area': [0, 0, 63, 63]}}},
    {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'nope', 'arguments': {}}},
    {'jsonrpc': '2.0', 'id': 6, 'method': 'ping'},
]
t0 = time.time()
p = subprocess.run([sys.executable, '-m', 'studio.agent', 'mcp'], cwd=ROOT, env=env, timeout=300,
                   input=''.join(json.dumps(r) + '\n' for r in reqs).encode(), capture_output=True)
lines = [json.loads(x) for x in p.stdout.decode('utf-8').splitlines() if x.strip()]
by = {r.get('id'): r for r in lines}
check('mcp: only JSON on stdout', len(lines) == 6, p.stderr.decode('utf-8', 'replace')[-800:])
check('mcp: initialize', by.get(1, {}).get('result', {}).get('serverInfo', {}).get('name') == 'narew-studio')
check('mcp: tools/list', len(by.get(2, {}).get('result', {}).get('tools', [])) == len(TOOLS))
check('mcp: world_open', not by.get(3, {}).get('result', {}).get('isError', True))
c4 = by.get(4, {}).get('result', {}).get('content', [])
check('mcp: picture inline', any(c.get('type') == 'image' and c.get('mimeType') == 'image/png' for c in c4))
check('mcp: unknown tool is an error result', by.get(5, {}).get('result', {}).get('isError'))
check('mcp: ping', by.get(6, {}).get('result') == {})
print(f'   mcp round trip {time.time() - t0:.1f}s')
p = subprocess.run([sys.executable, '-m', 'studio.agent', 'call', 'world_list', 'kind=prefab', 'query=choga'],
                   cwd=ROOT, env=env, capture_output=True, timeout=300)
out = json.loads(p.stdout.decode('utf-8')) if p.returncode == 0 else {}
check('cli call key=value', p.returncode == 0 and out.get('prefabs'), p.stderr.decode('utf-8', 'replace')[-500:])

print('\nrenders:', *RENDERS, sep='\n  ')
failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed' + (f'; FAILED: {failed}' if failed else ''))
sys.exit(1 if failed else 0)
