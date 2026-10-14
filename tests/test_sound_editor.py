"""Sound editor end to end through the real page.

    python tests/test_sound_editor.py <game dir> <scratch dir>
"""
import math
import os
import struct
import sys
import time
import wave

GAME, SCRATCH = sys.argv[1], os.path.abspath(sys.argv[2])
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
os.environ['USERPROFILE'] = os.path.join(SCRATCH, 'home')    # Documents\NarewStudio: shared settings, mods
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from narew.modfmt import ModInfo  # noqa: E402
from studio import audio as A  # noqa: E402
from studio.core import Studio  # noqa: E402
from studio.ui import common, page_sound  # noqa: E402
from studio.ui.glview import set_default_gl_format  # noqa: E402
from studio.ui.mainwindow import MainWindow  # noqa: E402

errors = []
sys.excepthook = lambda *exc: errors.append(exc)
QMessageBox.warning = staticmethod(lambda parent, title, msg, *a: errors.append(msg))
checks = []


def check(n, ok):
    checks.append((n, bool(ok)))


def pump(s=0.3):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


set_default_gl_format()
app = QApplication([])
common.apply_theme(app)
st = Studio()
st.open_game(GAME)
st.new_project(os.path.join(SCRATCH, 'projects'), ModInfo(id='sound-test', name='소리 테스트'))
win = MainWindow(st)
win.resize(1500, 900)
win.show()
win.open_page('sound')
page = win.page('sound')
pump()
names = page.sounds()
check('sounds listed', len(names) > 200)
tap = next(n for n in names if n.lower().endswith('\\ui\\tap.wav'))
page.open_sound(tap)
c0 = page.clip
check('wav opened', c0 is not None and c0.frames > 1000 and c0.rate == 44100)
pump()
win.grab().save(os.path.join(SCRATCH, 'sound_open.png'))

a, b = c0.frames // 4, c0.frames // 2
page.wave.sel = (a, b)
page.edit(lambda c, x, y: A.fade(c, x, y, True))
check('fade in on selection: starts silent', abs(float(page.clip.data[a, 0])) < 1e-6 and page.clip.frames == c0.frames)
page.edit(lambda c, x, y: A.gain(c, x, y, -6))
page.cut_sel()
check('cut removes the selection', page.clip.frames == c0.frames - (b - a) and page.clipboard is not None)
page.wave.cursor = a
page.paste()
check('paste puts it back', page.clip.frames == c0.frames)
page.undo()
page.undo()
check('undo x2 -> after gain, before cut', page.clip.frames == c0.frames and len(page.undo_stack) == 2)
page.redo()
page.redo()
check('redo x2', page.clip.frames == c0.frames and len(page.redo_stack) == 0)
page.wave.sel = None
n_before = page.clip.frames
page.push(A.speed(page.clip, 2.0), keep_view=False)
check('speed x2 halves the length', abs(page.clip.frames - n_before / 2) <= 1)

# import a mono 22.05 kHz tone: conformed to the original rate / channels
tone = os.path.join(SCRATCH, 'tone.wav')
with wave.open(tone, 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(22050)
    w.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(i / 10))) for i in range(22050)))
page_sound.open_file = lambda *a, **k: tone
page.import_file()
check('import conforms rate/channels', page.clip.rate == c0.rate and page.clip.channels == c0.channels
      and abs(page.clip.frames - 44100) <= 2)

# playback through the audio device
page.wave.cursor = 0
page.play()
pump(0.5)
moved = page.wave.play_pos
page.stop()
check('playback runs (playhead moved)', page.sink is None and (moved or 0) > 0)

page.save()
saved = st.need_project().find(tap)
with open(saved, 'rb') as f:
    info = A.info(f.read())
check('saved wav keeps format', info['rate'] == c0.rate and info['channels'] == c0.channels and info['subtype'] == c0.subtype
      and abs(info['frames'] - 44100) <= 2)
check('not dirty after save', not page.dirty())

# a long OGG (the libsndfile Vorbis encoder crashed on long single writes)
ogg = next(n for n in names if n.lower().endswith('.ogg') and A.decode(st.read(n)).duration > 10)
page.open_sound(ogg)
d0 = page.clip.duration
page.wave.sel = (page.clip.frames - page.clip.rate * 2, page.clip.frames)
page.edit(lambda c, x, y: A.fade(c, x, y, False))
page.save()
with open(st.need_project().find(ogg), 'rb') as f:
    oi = A.info(f.read())
check(f'long ogg ({d0:.1f}s) saved as Vorbis, same length', oi['subtype'] == 'VORBIS' and abs(oi['frames'] / oi['rate'] - d0) < 0.05)
pump(0.3)
win.grab().save(os.path.join(SCRATCH, 'sound_ogg.png'))
win.close()
for n, ok in checks:
    print('OK ' if ok else 'FAIL', n)
for e in errors:
    print('ERROR', e)
ok = all(c for _, c in checks) and not errors
print('ALL OK' if ok else 'SOME CHECKS FAILED')
sys.exit(0 if ok else 1)
