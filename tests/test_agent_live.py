"""A NarewStudio window and an AI agent (separate process, CLI) working on the same project.

    python tests/test_agent_live.py <game dir> <scratch dir>

The window opens map\\64_0.bwr in the world editor; the agent (python -m studio.agent run ...) opens the same
world, builds on it and saves -> the window reloads it (camera kept) and shows the new bricks.  Also checked:
the window's heartbeat (what an agent sees with studio_view), studio_show opening a GUI layout, and the
window following an agent's project switch.  Never touches the real game install.
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
USERBASE = os.environ.get('PYTHONUSERBASE') or os.path.join(os.environ.get('APPDATA', ''), 'Python')
os.environ.update(ENV)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GAME = os.path.join(SCRATCH, 'game')
os.makedirs(os.path.join(GAME, 'data'))
for stem in ('xml', 'default', 'map', 'gui', 'gui_skin', 'font', 'architecture'):
    shutil.copy2(os.path.join(GAME_SRC, 'data', stem + '.pack'), os.path.join(GAME, 'data', stem + '.pack'))
shutil.copytree(os.path.join(GAME_SRC, 'Language'), os.path.join(GAME, 'Language'))
open(os.path.join(GAME, 'IsletClient_release(x64).exe'), 'wb').close()

from PySide6.QtWidgets import QApplication  # noqa: E402

from narew.modfmt import ModInfo  # noqa: E402
from studio.agent import live  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.ui.common import apply_theme  # noqa: E402
from studio.ui.glview import set_default_gl_format  # noqa: E402
from studio.ui.mainwindow import MainWindow  # noqa: E402

checks = []


def check(name, ok, info=''):
    checks.append((name, bool(ok)))
    print(('OK  ' if ok else 'FAIL') + '  ' + name + (f'  [{info}]' if info and not ok else ''), flush=True)


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.02)
    return bool(until and until())


def agent(steps):
    """Run tool calls in a separate agent process (as an MCP/CLI agent would)."""
    path = os.path.join(SCRATCH, f'steps_{time.time_ns()}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(steps, f, ensure_ascii=False)
    env = dict(os.environ, **ENV, PYTHONUSERBASE=USERBASE, PYTHONIOENCODING='utf-8')
    p = subprocess.Popen([sys.executable, '-m', 'studio.agent', 'run', path], cwd=ROOT, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while p.poll() is None:                     # keep the window alive while the agent works
        pump(0.1)
    out, err = p.communicate()
    try:
        res = json.loads(out.decode('utf-8'))
    except ValueError:
        res = []
    if p.returncode != 0:
        print(out.decode('utf-8', 'replace')[-1500:], err.decode('utf-8', 'replace')[-1500:])
    return p.returncode, res


set_default_gl_format()
app = QApplication([])
apply_theme(app)
st = Studio()
st.open_game(GAME)
st.new_project(st.default_projects_dir(), ModInfo(id='live-test', name='live test'))
win = MainWindow(st)
win.resize(1200, 800)
win.show()
win.open_page('world', name='map\\64_0.bwr')
wp = win.page('world')
check('window opened the world', pump(30, lambda: wp.doc is not None and wp.view.store is not None
                                       and len(wp.view.store.regions) > 4))
pump(2.5)
hb = live.gui_state()
check('heartbeat says world page', hb and hb.get('page') == 'world', hb)
check('heartbeat names the open world', hb and (hb.get('open') or {}).get('world', {}).get('name') == 'map\\64_0.bwr')
cam_before = list(wp.view.cam.target)

# the agent edits what the window shows (world_open with no args) and saves
X, Z = int(cam_before[0]), int(-cam_before[2])
code, res = agent([
    {'tool': 'studio_view'},
    {'tool': 'world_open'},
    {'tool': 'world_ground', 'args': {'session': 'w1', 'x': X, 'z': Z}},
])
check('agent sees the window', code == 0 and res[0]['result'].get('running'), res[:1])
check('agent opened the same world', code == 0 and res[1]['result']['name'] == 'map\\64_0.bwr')
gy = res[2]['result']['place_at_y'] if code == 0 else 100
code, res = agent([
    {'tool': 'world_open'},
    {'tool': 'world_fill', 'args': {'session': 'w1', 'box': [X - 2, gy, Z - 2, X + 2, gy + 6, Z + 2], 'brick': 60,
                                    'mode': 'hollow'}},
    {'tool': 'world_save', 'args': {'session': 'w1'}},
])
check('agent built and saved', code == 0 and res[-1].get('ok'), res[-1:] if res else code)
reloaded = pump(15, lambda: wp.doc is not None and wp.view.store is not None
                and wp.view.store.get(X - 2, gy + 3, Z) == 60)
check('window reloaded the world with the agent\'s bricks', reloaded)
pump(1.0)
check('camera kept', [round(float(a), 3) for a in wp.view.cam.target] == [round(float(a), 3) for a in cam_before],
      (list(wp.view.cam.target), cam_before))
check('window title shows the mod change', 'modified' in wp.title.text() or 'new' in wp.title.text(), wp.title.text())

# studio_show: the agent asks the window to open a GUI layout
code, res = agent([{'tool': 'studio_show', 'args': {'page': 'gui', 'name': 'Gui\\inventory.xml'}}])
check('studio_show accepted', code == 0)
gp = None
ok = pump(15, lambda: win.current == 'gui' and win.pages['gui'].name is not None)
check('window switched to the GUI layout', ok and win.pages['gui'].name.lower() == 'gui\\inventory.xml')

# the agent edits that layout -> the window reloads it
code, res = agent([{'tool': 'gui_edit', 'args': {'name': 'inventory', 'ops': [
    {'op': 'add', 'parent': 0, 'kind': 'extended_text', 'x': 5, 'y': 5, 'w': 120, 'h': 20, 'text': 'live'}]}}])
gp = win.pages['gui']
check('window reloaded the edited layout', pump(15, lambda: 'live' in gp.text))

# the agent switches project -> the window follows
code, res = agent([{'tool': 'project_new', 'args': {'name': 'second', 'id': 'second-mod'}}])
check('window followed the project switch', pump(15, lambda: st.project and st.project.root.endswith('second-mod')),
      st.project.root if st.project else None)

win.close()
pump(0.5)
check('heartbeat removed on close', live.gui_state() is None)
failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed' + (f'; FAILED: {failed}' if failed else ''))
sys.exit(1 if failed else 0)
