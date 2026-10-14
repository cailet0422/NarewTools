"""End-to-end check of studio.creator on a scratch project.

    python tests/test_creator.py <game dir> <scratch dir>
APPDATA is redirected into the scratch dir so user settings are untouched.
"""
import os
import re
import sys

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
os.environ['USERPROFILE'] = os.path.join(SCRATCH, 'home')    # Documents\NarewStudio: shared settings, mods
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from narew.modfmt import ModInfo  # noqa: E402
from studio import tables, textcodec  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.creator import Creator, Names, Recipe  # noqa: E402

# 2.4.0 and older kept the settings in %APPDATA%\NarewStudio (a packaged app sees its own copy): taken over
os.makedirs(os.path.join(SCRATCH, 'appdata', 'NarewStudio'), exist_ok=True)
with open(os.path.join(SCRATCH, 'appdata', 'NarewStudio', 'settings.json'), 'w', encoding='utf-8') as f:
    f.write('{"last_page": "blocks"}')
st = Studio()
adopted = (st.settings_path == os.path.join(SCRATCH, 'home', 'NarewStudio', 'settings.json')
           and st.settings.get('last_page') == 'blocks')
st.open_game(GAME)
root = st.new_project(os.path.join(SCRATCH, 'projects'), ModInfo(id='creator-test', name='생성 테스트'))
cr = Creator(st)

# textures for the block
for name, color in (('top', (90, 200, 120)), ('side', (140, 90, 60))):
    im = Image.new('RGBA', (128, 128), color + (255,))
    for y in range(0, 128, 16):
        for x in range(0, 128, 16):
            if (x + y) // 16 % 2:
                im.paste((min(255, color[0] + 40), color[1], color[2], 255), (x, y, x + 16, y + 16))
    im.save(os.path.join(SCRATCH, f'tex_{name}.png'))

checks = [('settings in Documents\\NarewStudio, old ones taken over', adopted)]
free_b = cr.free_brick_ids()
free_p = cr.free_product_ids()
print('free brick ids', len(free_b), free_b[:5], '| free product ids', len(free_p), free_p[:3], free_p[-3:],
      '| free item ids from 19300', len(cr.free_item_ids()), '| free craft ids', len(cr.free_craft_ids()))
checks.append(('brick ids exclude legacy 171-191', not any(171 <= i <= 191 for i in free_b)))
checks.append(('product ids <= 1023', max(free_p) <= 1023))

bid = free_b[0]
r1 = cr.new_block(brick_id=bid, names=Names('테스트 블록', 'Test Block', '시험용 블록', 'A test block'),
                  template_brick=24, textures={'top': os.path.join(SCRATCH, 'tex_top.png'),
                                               'side': os.path.join(SCRATCH, 'tex_side.png')},
                  recipe=Recipe(station=3, ingredients=[(2, 4)], count=4))
print('block', r1)

iid = cr.free_item_ids()[0]
r2 = cr.new_furniture(product_id=free_p[-1], item_id=iid, names=Names('테스트 의자', 'Test Chair'),
                      template_product=1, model_name='mod_test_chair.nwmt', copy_model='Object\\obj_chair001.nwmt',
                      retexture={'Object\\chair.png': os.path.join(SCRATCH, 'tex_top.png')},
                      icon=os.path.join(SCRATCH, 'tex_side.png'), recipe=Recipe(station=0, ingredients=[(52, 2)]))
print('furniture', r2)

# copy from a sub-folder model without retexturing: textures must still resolve from Object\
r2b = cr.new_furniture(product_id=cr.free_product_ids()[-1], item_id=cr.free_item_ids()[0],
                       names=Names('테스트 벤치', 'Test Bench'), template_product=1, model_name='mod_test_bench.nwmt',
                       copy_model='Object\\KEH\\obj_bench.nwmt', icon=os.path.join(SCRATCH, 'tex_side.png'))
from studio import svc_blocks  # noqa: E402
bench_tex = svc_blocks.model_textures(st, r2b['model'])
print('bench textures', bench_tex['textures'])

iid2 = cr.free_item_ids()[0]
potion = next(i for i in cr.items('potion'))
r3 = cr.new_item(item_id=iid2, names=Names('테스트 물약', 'Test Potion'), template_item=potion['id'],
                 icon=os.path.join(SCRATCH, 'tex_top.png'))
print('item', r3)

snd = os.path.join(SCRATCH, 'beep.wav')
import struct, math  # noqa: E401,E402
frames = b''.join(struct.pack('<h', int(8000 * math.sin(i / 8))) for i in range(8000))
with open(snd, 'wb') as f:
    f.write(b'RIFF' + struct.pack('<I', 36 + len(frames)) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 1, 8000, 16000, 2, 16)
            + b'data' + struct.pack('<I', len(frames)) + frames)
slots = cr.sky_slots()
r4 = cr.new_sound(src=snd, entry='Sound\\mod\\beep.wav', sky=[(slots[0]['time'], slots[0]['weather'])], ui=['click'])
print('sound', r4)

# ---- verify merged views
def has(table, pattern):
    return re.search(pattern, textcodec.decode(st.read(table))[0]) is not None

checks += [
    ('brick in brick_table', has(tables.BRICK_TABLE, rf'<brick id="{bid}"[^>]*>\s*<name>Test Block</name>')),
    ('brick item in item_table', has('XML\\item_table.xml', rf'<item id="{bid}">\s*<name>Test Block')),
    ('furniture product', has(tables.PRODUCT_TABLE, rf'<product id="{free_p[-1]}"[^>]*>\s*<name>테스트 의자</name>')),
    ('furniture item', has('XML\\item_table.xml', rf'<item id="{iid}">')),
    ('potion item', has('XML\\item_table.xml', rf'<item id="{iid2}">')),
    ('item_name string', has('Language\\string.xml', rf'<string table="item_name" index="{iid}">\s*<kor>테스트 의자</kor>')),
    ('recipe', has('XML\\crafting.xml', rf'item="{iid}"')),
    ('sky bgm', has('XML\\sky.xml', r'Sound\\mod\\beep\.wav')),
    ('ui sound', has('Gui\\sound.xml', r'sound\\mod\\beep\.wav')),
    ('brick icon', st.exists(r1['icon'])),
    ('furniture model', st.exists('Object\\mod_test_chair.nwmt')),
    ('copied sub-folder model keeps textures',
     bench_tex['textures'] and bench_tex['textures'] == svc_blocks.model_textures(st, 'Object\\KEH\\obj_bench.nwmt')['textures']),
    ('retextured file', any(n.lower().startswith('object\\mod_test_chair_') for n, _ in st.project.iter_files())),
    ('tables still parse', all(tables.bricks(st.read(tables.BRICK_TABLE)) and
                               tables.products(st.read(tables.PRODUCT_TABLE)) for _ in [0])),
]
# atlas tiles really changed + brick references them
br = next(b for b in tables.bricks(st.read(tables.BRICK_TABLE)) if b['id'] == bid)
checks.append(('brick uses new tiles', br['tiles'] == r1['tiles']))
# merged string.xml keeps its size order of magnitude (no accidental rewrite)
g = len(st.read('Language\\string.xml', 'game'))
m = len(st.read('Language\\string.xml'))
checks.append(('string.xml grew a little', 0 < m - g < 20000))
Image.open(os.path.join(root, 'files', *r1['icon'].split('\\'))).resize((256, 256), Image.NEAREST).save(
    os.path.join(SCRATCH, 'brick_icon_big.png'))
print('added records:', len(cr.added()))
bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
print('ALL OK' if not bad else f'FAILED: {bad}')
sys.exit(1 if bad else 0)
