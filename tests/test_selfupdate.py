"""Self-update (studio.selfupdate) against files that play the GitHub releases API (file:// URLs).

    python tests/test_selfupdate.py <scratch dir>

Never touches the network or the user's settings (APPDATA/LOCALAPPDATA point into the scratch folder):
version compare, check (newer / same / pre-release / missing assets / asset outside the allowed prefix),
download (progress, SHA-256, unpack, bad hash, zip-slip), running_from / wait_exit, apply_job (backup,
copy, new files, rollback on a failing copy, waiting for the old program to quit, restart), report, cleanup.
"""
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile

SCRATCH = os.path.abspath(sys.argv[1])
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(SCRATCH)
os.environ['APPDATA'] = os.path.join(SCRATCH, 'appdata')
os.environ['LOCALAPPDATA'] = os.path.join(SCRATCH, 'localappdata')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from narew.version import REPO, VERSION  # noqa: E402
from studio import selfupdate as su  # noqa: E402
from studio.core import StudioError  # noqa: E402

checks = []


def check(name, ok, info=''):
    checks.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{info}]' if info and not ok else ''), flush=True)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except StudioError as ex:
        return str(ex)
    return None


# ------------------------------------------------------------------ versions
check('parse_version', su.parse_version('v2.10.1') == (2, 10, 1) and su.parse_version('2.2') == (2, 2)
      and su.parse_version('latest') == () and su.parse_version('2.2.0-beta') == ())
check('newer compares numbers, not text', su.newer('2.10.0', '2.9.9') and not su.newer('2.2.0', '2.2.0')
      and not su.newer('2.1.9', '2.2.0'))
check('asset prefix is the project releases', su.ASSET_PREFIX == f'https://github.com/{REPO}/releases/download/')

# ------------------------------------------------------------------ a fake release
NEW = '.'.join(map(str, su.parse_version(VERSION)[:-1] + (su.parse_version(VERSION)[-1] + 1,)))
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    for p in su.PROGRAMS:
        z.writestr(f'NarewTools/{p}', f'new {p}')
    z.writestr('NarewTools/_internal/lib.dll', 'new lib')
    z.writestr('NarewTools/_internal/added.pyd', 'added in the new version')
    z.writestr('NarewTools/사용법.txt', 'guide')
ZIP = buf.getvalue()
SHA = hashlib.sha256(ZIP).hexdigest()
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    for p in su.PROGRAMS:
        z.writestr(f'NarewTools/{p}', 'x')
    z.writestr('../evil.txt', 'outside')
EVIL = buf.getvalue()
SRV = os.path.join(SCRATCH, 'srv')          # plays GitHub through file:// URLs (no sockets: some PCs' security
os.makedirs(os.path.join(SRV, 'dl'))        # software resets local HTTP connections); urllib reads both the same


class Served(dict):
    """url path -> bytes (dicts as JSON), written under SRV."""
    def __setitem__(self, k, v):
        v = json.dumps(v).encode() if isinstance(v, dict) else v
        super().__setitem__(k, v)
        with open(os.path.join(SRV, *k.strip('/').split('/')), 'wb') as f:
            f.write(v)


FILES = RELEASES = Served()
BASE = pathlib.Path(SRV).as_uri()
DL = BASE + '/dl/'


def release(tag, assets=True, url=DL, **kw):
    a = [{'name': f'NarewTools-{tag.lstrip("v")}.zip', 'browser_download_url': url + 'z.zip', 'size': len(ZIP)},
         {'name': f'NarewTools-{tag.lstrip("v")}.zip.sha256', 'browser_download_url': url + 'z.sha256'}]
    return {'tag_name': tag, 'body': '- 새 기능', 'html_url': BASE + '/page', 'assets': a if assets else [], **kw}


FILES['/dl/z.zip'] = ZIP
FILES['/dl/z.sha256'] = f'{SHA}  NarewTools-{NEW}.zip\n'.encode()
RELEASES['/new'] = release('v' + NEW)
RELEASES['/same'] = release('v' + VERSION)
RELEASES['/pre'] = release('v' + NEW, prerelease=True)
RELEASES['/noassets'] = release('v' + NEW, assets=False)
RELEASES['/foreign'] = release('v' + NEW, url='http://example.invalid/')

# ------------------------------------------------------------------ check
check('auto check is due at first', su.due())
info = su.check(BASE + '/new', DL)
check('newer release found', info and info['version'] == NEW and info['zip_url'] == DL + 'z.zip'
      and info['notes'] == '- 새 기능', info)
check('the check time is remembered (not due right after)', not su.due() and su.settings().get('latest') == NEW)
check('same version: nothing', su.check(BASE + '/same', DL) is None)
check('pre-release ignored', su.check(BASE + '/pre', DL) is None)
na = su.check(BASE + '/noassets', DL)
check('release without update files: offered without zip', na and na['zip_url'] is None)
check('download of such a release refused', raises(su.download, na) is not None)
check('asset outside the allowed prefix refused', raises(su.check, BASE + '/foreign', DL) is not None)
check('network error reported in Korean', '확인하지 못했습니다' in (raises(su.check, BASE + '/missing', DL) or ''))
su.set_option(auto_check=False, last_check=0)
check('auto check off: not due', not su.due())
su.set_option(auto_check=True)

# ------------------------------------------------------------------ download
prog = []
job = su.download(info, lambda d, t, m='': prog.append((d, t)))
check('download: progress reported', prog and prog[-1] == (len(ZIP), len(ZIP)), prog[-1:])
check('download: unpacked program folder', os.path.isfile(os.path.join(job['staged'], 'NarewStudio.exe'))
      and os.path.basename(job['staged']) == 'NarewTools' and job['version'] == NEW)
check('download: zip removed after unpacking', not any(f.endswith('.zip') for f in os.listdir(job['work'])))
FILES['/dl/z.sha256'] = ('0' * 64 + '\n').encode()
check('download: wrong SHA-256 refused', 'SHA-256' in (raises(su.download, info) or ''))
FILES['/dl/z.sha256'] = b'not a hash'
check('download: malformed .sha256 refused', raises(su.download, info) is not None)
FILES['/dl/z.zip'] = EVIL
FILES['/dl/z.sha256'] = hashlib.sha256(EVIL).hexdigest().encode()
check('download: zip-slip path refused', '밖' in (raises(su.download, info) or ''))
check('nothing written outside the update folder', not os.path.exists(os.path.join(su.update_root(), NEW, 'evil.txt')))
FILES['/dl/z.zip'], FILES['/dl/z.sha256'] = ZIP, SHA.encode()
job = su.download(info)

# ------------------------------------------------------------------ processes
here = os.path.dirname(sys.executable)
mine = su.running_from(here)
check('running_from finds this process', any(pid == os.getpid() for pid, _ in mine), mine[:3])
check('running_from excludes', all(pid != os.getpid() for pid, _ in su.running_from(here, exclude={os.getpid()})))
empty = os.path.join(SCRATCH, 'empty')
os.makedirs(empty)
check('running_from: nothing in an empty folder', su.running_from(empty) == [])
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'])
t0 = time.time()
check('wait_exit waits for a running process', su.wait_exit(p.pid, 20) and time.time() - t0 > 0.5)
check('wait_exit: ended process', su.wait_exit(p.pid, 1))
q = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
check('wait_exit times out', not su.wait_exit(q.pid, 0.3))
q.kill()


# ------------------------------------------------------------------ apply
def fake_install(name):
    d = os.path.join(SCRATCH, name)
    os.makedirs(os.path.join(d, '_internal'))
    for rel in su.PROGRAMS + ('_internal/lib.dll', 'my notes.txt'):
        with open(os.path.join(d, rel), 'w', encoding='utf-8') as f:
            f.write('old ' + rel)
    return d


def read(*p):
    with open(os.path.join(*p), encoding='utf-8') as f:
        return f.read()


def job_file(install, pid=0, restart=None):
    spec = {'version': NEW, 'old_version': VERSION, 'staged': job['staged'], 'install': install, 'pid': pid,
            'restart': restart, 'backup': os.path.join(SCRATCH, f'backup-{os.path.basename(install)}'),
            'log': os.path.join(SCRATCH, f'{os.path.basename(install)}.log')}
    path = os.path.join(SCRATCH, f'{os.path.basename(install)}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(spec, f)
    return path, spec


inst = fake_install('install')
started = []
slow = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'])
path, spec = job_file(inst, pid=slow.pid, restart='NarewStudio.exe')
t0 = time.time()
rc = su.apply_job(path, start=lambda cmd, **kw: started.append(cmd))
check('apply: waited for the old program', time.time() - t0 > 0.5)
check('apply: ok', rc == 0, read(spec['log']))
check('apply: files replaced', read(inst, 'NarewStudio.exe') == 'new NarewStudio.exe'
      and read(inst, '_internal', 'lib.dll') == 'new lib')
check('apply: new file added', read(inst, '_internal', 'added.pyd') == 'added in the new version')
check('apply: user file kept', read(inst, 'my notes.txt') == 'old my notes.txt')
check('apply: replaced files backed up', read(spec['backup'], 'NarewStudio.exe') == 'old NarewStudio.exe'
      and not os.path.exists(os.path.join(spec['backup'], 'my notes.txt')))
check('apply: program started again', started == [[os.path.join(inst, 'NarewStudio.exe')]], started)
r = su.take_report()
check('report: ok once', r and r['ok'] and r['version'] == NEW and su.take_report() is None, r)

inst2 = fake_install('install2')
path, spec = job_file(inst2)
n = [0]


def flaky(src, dst):
    n[0] += 1
    if n[0] == 5:                                     # after the new added.pyd, at lib.dll
        raise PermissionError('locked')
    shutil.copy2(src, dst)


def failing_retry(fn, *a, **kw):                    # no 20 s of retries in the test
    return fn(*a)


su._retry, real_retry = failing_retry, su._retry
rc = su.apply_job(path, copy=flaky)
su._retry = real_retry
check('rollback: reported failure', rc == 1)
check('rollback: old files back', all(read(inst2, rel) == 'old ' + rel for rel in su.PROGRAMS + ('_internal/lib.dll',)))
check('rollback: added file removed again', not os.path.exists(os.path.join(inst2, '_internal', 'added.pyd')))
r = su.take_report()
check('report: failure with the reason', r and not r['ok'] and '되돌렸습니다' in r.get('error', ''), r)

# ------------------------------------------------------------------ cleanup
root = su.update_root()
for name in (VERSION, '0.0.1', NEW, 'backup-0.0.1', 'backup-' + VERSION):
    os.makedirs(os.path.join(root, name), exist_ok=True)
su.cleanup()
left = sorted(os.listdir(root))
check('cleanup: old downloads and older backups removed, newer download kept',
      left == sorted([NEW, 'backup-' + VERSION]), left)
check('not frozen: no self-replace from source', su.frozen_dir() is None)

bad = [n for n, ok in checks if not ok]
print(f'{len(checks) - len(bad)}/{len(checks)} passed' + (': FAILED ' + ', '.join(bad) if bad else ''))
sys.exit(1 if bad else 0)
