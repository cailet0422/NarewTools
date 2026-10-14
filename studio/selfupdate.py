"""Self-update of the NarewTools folder (NarewStudio / NarewModManager / NarewAgent) from GitHub releases.

check()      latest release of narew.version.REPO -> its NarewTools-<v>.zip and .zip.sha256 assets when it is
             newer than this build (drafts and pre-releases are ignored)
download()   the zip -> %LOCALAPPDATA%\\NarewTools\\update\\<v>\\files (SHA-256 checked, unpacked zip-slip safe)
launch()     starts NarewAgent.exe *of the new version* from that unpacked folder - nothing in the install
             folder is in use by it - with ``--apply-update <job.json>``; the caller then quits
apply_job()  (in that process) waits until no program runs from the install folder any more, backs up the
             files it replaces, copies the new ones over (rolling back on any error), records the result
             and starts the program again

Only a frozen build (the PyInstaller folder with all three programs) replaces itself; from source the check
still reports new versions.  Settings and the last result: %APPDATA%\\NarewTools\\update.json, shared by both
programs.  Files the user put into the folder are never deleted (old files of a previous version stay too).
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from narew.paths import appdata, load_json, save_json
from narew.version import REPO, VERSION

from .core import StudioError

API = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET_PREFIX = f'https://github.com/{REPO}/releases/download/'
PAGE = f'https://github.com/{REPO}/releases'
PROGRAMS = ('NarewStudio.exe', 'NarewModManager.exe', 'NarewAgent.exe')
CHECK_EVERY = 6 * 3600            # automatic checks at most this often (seconds)
UA = f'NarewTools/{VERSION} (+https://github.com/{REPO})'


# --------------------------------------------------------------------------- versions and settings

def parse_version(text: str) -> tuple[int, ...]:
    """'v2.10.1' -> (2, 10, 1); anything else -> ()."""
    m = re.match(r'^\s*[vV]?(\d+(?:\.\d+)*)\s*$', text or '')
    return tuple(int(p) for p in m.group(1).split('.')) if m else ()


def newer(a: str, b: str) -> bool:
    return parse_version(a) > parse_version(b)


def settings_path() -> str:
    return os.path.join(appdata('NarewTools'), 'update.json')


def settings() -> dict:
    s = load_json(settings_path(), {})
    return s if isinstance(s, dict) else {}


def _save(s: dict):
    save_json(settings_path(), s)


def set_option(**kw):
    """auto_check=bool, skip='2.3.0' (do not offer that version automatically)."""
    s = settings()
    s.update(kw)
    _save(s)


def due(now: float | None = None) -> bool:
    """An automatic check is wanted now (on, and the last one is older than CHECK_EVERY)."""
    s = settings()
    now = time.time() if now is None else now
    return s.get('auto_check', True) and now - float(s.get('last_check') or 0) >= CHECK_EVERY


def take_report() -> dict | None:
    """The result of the last update (once): {'version', 'from', 'ok', 'error'?, 'log'}."""
    s = settings()
    r = s.pop('applied', None)
    if r is not None:
        _save(s)
    return r


def update_root() -> str:
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or os.path.expanduser('~')
    p = os.path.join(base, 'NarewTools', 'update')
    os.makedirs(p, exist_ok=True)
    return p


def cleanup(root: str | None = None):
    """Remove unpacked downloads of versions not newer than this one (an update that went in, or an old
    one); backups are kept, only the newest."""
    root = root or update_root()
    backups = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        if name.startswith('backup-'):
            backups.append((parse_version(name[7:]), p))
        elif parse_version(name) and not newer(name, VERSION):
            shutil.rmtree(p, ignore_errors=True)
    for _, p in sorted(backups)[:-1]:
        shutil.rmtree(p, ignore_errors=True)


def frozen_dir() -> str | None:
    """The NarewTools folder this program runs from when it is the built (frozen) one, else None."""
    if not getattr(sys, 'frozen', False):
        return None
    d = os.path.dirname(os.path.abspath(sys.executable))
    return d if all(os.path.isfile(os.path.join(d, p)) for p in PROGRAMS) else None


def writable(folder: str) -> bool:
    try:
        p = os.path.join(folder, f'.narew-write-test-{os.getpid()}')
        with open(p, 'wb'):
            pass
        os.remove(p)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- check and download

def _open(url: str, timeout: float, tries: int = 3):
    """GET url; a dropped connection is tried again (an HTTP error answer is not)."""
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'})
    for i in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(0.5 * (i + 1))


def check(api: str = API, prefix: str = ASSET_PREFIX, current: str = VERSION, timeout: float = 15) -> dict | None:
    """The newest release when it is newer than current: {'version', 'tag', 'notes', 'page', 'zip_url',
    'sha_url', 'size'} (zip_url None = that release has no update files), else None."""
    try:
        with _open(api, timeout) as r:
            rel = json.loads(r.read().decode('utf-8'))
    except (OSError, ValueError) as ex:
        raise StudioError(f'업데이트를 확인하지 못했습니다 (인터넷 연결을 확인하세요): {ex}')
    s = settings()
    s['last_check'] = time.time()
    tag = str(rel.get('tag_name') or '')
    if not parse_version(tag) or rel.get('draft') or rel.get('prerelease'):
        _save(s)
        return None
    ver = '.'.join(map(str, parse_version(tag)))
    s['latest'] = ver
    _save(s)
    if not newer(ver, current):
        return None
    assets = {a.get('name'): a for a in rel.get('assets') or [] if isinstance(a, dict)}
    info = {'version': ver, 'tag': tag, 'notes': str(rel.get('body') or '').strip(),
            'page': rel.get('html_url') or PAGE, 'zip_url': None, 'sha_url': None, 'size': 0}
    z, h = assets.get(f'NarewTools-{ver}.zip'), assets.get(f'NarewTools-{ver}.zip.sha256')
    if z and h:
        urls = [str(a.get('browser_download_url') or '') for a in (z, h)]
        if not all(u.startswith(prefix) for u in urls):
            raise StudioError(f'업데이트 파일 주소가 {prefix} 가 아닙니다: {urls}')
        info.update(zip_url=urls[0], sha_url=urls[1], size=int(z.get('size') or 0))
    return info


def _program_dir(top: str) -> str | None:
    for d, _, files in os.walk(top):
        if all(p in files for p in PROGRAMS):
            return d
    return None


def download(info: dict, progress=None, root: str | None = None, timeout: float = 30) -> dict:
    """Download, verify and unpack the release; {'version', 'staged' (folder with the programs), 'work'}."""
    if not info.get('zip_url'):
        raise StudioError(f"{info.get('version')} 릴리스에는 자동 업데이트 파일이 없습니다. 릴리스 페이지에서 받으세요:\n"
                          f"{info.get('page') or PAGE}")
    work = os.path.join(root or update_root(), info['version'])
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        with _open(info['sha_url'], timeout) as r:
            expected = r.read().decode('ascii', 'replace').split()
        if not expected or not re.fullmatch(r'[0-9a-fA-F]{64}', expected[0]):
            raise StudioError('업데이트 확인용 SHA-256 파일이 올바르지 않습니다')
        expected = expected[0].lower()
        zpath = os.path.join(work, f"NarewTools-{info['version']}.zip")
        h, done = hashlib.sha256(), 0
        with _open(info['zip_url'], timeout) as r, open(zpath, 'wb') as f:
            total = int(r.headers.get('Content-Length') or info.get('size') or 0)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, f"업데이트 받는 중 {done / 2 ** 20:.0f} / {total / 2 ** 20:.0f} MB")
    except OSError as ex:
        raise StudioError(f'업데이트를 받지 못했습니다: {ex}')
    if h.hexdigest() != expected:
        raise StudioError('받은 파일이 손상되었습니다 (SHA-256이 맞지 않음). 다시 시도하세요.')
    out = os.path.join(work, 'files')
    real = os.path.realpath(out) + os.sep
    try:
        with zipfile.ZipFile(zpath) as z:
            for m in z.infolist():
                if not os.path.realpath(os.path.join(out, m.filename)).startswith(real):
                    raise StudioError(f'업데이트 파일에 폴더 밖을 가리키는 경로가 있습니다: {m.filename}')
            z.extractall(out)
    except zipfile.BadZipFile as ex:
        raise StudioError(f'업데이트 파일을 풀 수 없습니다: {ex}')
    os.remove(zpath)
    staged = _program_dir(out)
    if not staged:
        raise StudioError('업데이트 파일에 NarewTools 프로그램이 없습니다')
    return {'version': info['version'], 'staged': staged, 'work': work}


# --------------------------------------------------------------------------- processes (Windows)

def running_from(folder: str, exclude=()) -> list[tuple[int, str]]:
    """(pid, program path) of the running processes whose program file is inside folder."""
    if os.name != 'nt':
        return []
    from ctypes import wintypes
    psapi = ctypes.WinDLL('psapi')
    k32 = ctypes.WinDLL('kernel32')
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    pids = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    root = os.path.normcase(os.path.abspath(folder)).rstrip('\\/') + os.sep
    out = []
    for pid in pids[:needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid or pid in exclude:
            continue
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) \
                    and os.path.normcase(buf.value).startswith(root):
                out.append((int(pid), buf.value))
        finally:
            k32.CloseHandle(h)
    return out


def wait_exit(pid: int, timeout: float) -> bool:
    """Wait until process pid has ended (True), or timeout (False).  A pid that cannot be opened has ended."""
    if os.name != 'nt' or not pid:
        return True
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32')
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    h = k32.OpenProcess(0x00100000, False, pid)            # SYNCHRONIZE
    if not h:
        return True
    try:
        return k32.WaitForSingleObject(wintypes.HANDLE(h), int(timeout * 1000)) == 0
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


# --------------------------------------------------------------------------- install

def launch(job: dict, install: str, restart: str | None, pid: int | None = None) -> str:
    """Start the new version's NarewAgent.exe to put job['staged'] into install once this process (pid) has
    quit; restart = program file name to start afterwards.  Returns the job file."""
    agent = os.path.join(job['staged'], 'NarewAgent.exe')
    if not os.path.isfile(agent):
        raise StudioError('받은 업데이트에 NarewAgent.exe가 없습니다')
    spec = {'version': job['version'], 'old_version': VERSION, 'staged': job['staged'],
            'install': os.path.abspath(install), 'pid': pid or os.getpid(), 'restart': restart,
            'backup': os.path.join(os.path.dirname(job['work']), f'backup-{VERSION}'),
            'log': os.path.join(job['work'], 'update.log')}
    path = os.path.join(job['work'], 'job.json')
    save_json(path, spec)
    flags = 0x08000000 | 0x00000200                        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    for extra in (0x01000000, 0):                          # CREATE_BREAKAWAY_FROM_JOB where the job allows it
        try:
            subprocess.Popen([agent, '--apply-update', path], cwd=job['staged'], creationflags=flags | extra,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
            return path
        except OSError:
            if not extra:
                raise
    return path


def _files(top: str) -> list[str]:
    return sorted(os.path.relpath(os.path.join(d, f), top) for d, _, fs in os.walk(top) for f in fs)


def _retry(fn, *args, tries: int = 40, wait: float = 0.5):
    for i in range(tries):
        try:
            return fn(*args)
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def _put(src: str, dst: str, copy):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    copy(src, dst)


def apply_job(path: str, copy=shutil.copy2, wait: float = 120, start=subprocess.Popen) -> int:
    """Run by the new version's NarewAgent.exe (--apply-update): replace the install folder's files."""
    job = load_json(path, None)
    if not isinstance(job, dict):
        return 2
    os.makedirs(os.path.dirname(job['log']), exist_ok=True)
    log = open(job['log'], 'a', encoding='utf-8')

    def say(msg):
        log.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')
        log.flush()

    result = {'version': job['version'], 'from': job.get('old_version'), 'time': time.time(), 'log': job['log']}
    install, staged, backup = job['install'], job['staged'], job['backup']
    say(f"update {job.get('old_version')} -> {job['version']}: {staged} -> {install}")
    try:
        if not wait_exit(int(job.get('pid') or 0), wait):
            raise StudioError('프로그램이 끝나지 않았습니다')
        deadline = time.time() + wait
        while True:
            busy = running_from(install, exclude={os.getpid()})
            if not busy:
                break
            if time.time() > deadline:
                raise StudioError('NarewTools 프로그램이 아직 실행 중입니다: '
                                  + ', '.join(sorted({os.path.basename(p) for _, p in busy})))
            time.sleep(1)
        files = _files(staged)
        shutil.rmtree(backup, ignore_errors=True)
        kept = [rel for rel in files if os.path.isfile(os.path.join(install, rel))]
        for rel in kept:
            _put(os.path.join(install, rel), os.path.join(backup, rel), shutil.copy2)
        say(f'backup: {len(kept)} files -> {backup}')
        done = []
        try:
            for rel in files:
                _retry(_put, os.path.join(staged, rel), os.path.join(install, rel), copy)
                done.append(rel)
        except Exception as ex:
            say(f'copy failed ({ex}); rolling back {len(done)} files')
            for rel in reversed(done):
                try:
                    if rel in kept:
                        _retry(_put, os.path.join(backup, rel), os.path.join(install, rel), shutil.copy2)
                    else:
                        os.remove(os.path.join(install, rel))
                except OSError as ex2:
                    say(f'rollback of {rel} failed: {ex2}')
            raise StudioError(f'파일을 바꾸지 못해 원래대로 되돌렸습니다: {ex}')
        result['ok'] = True
        say(f'done: {len(files)} files')
    except Exception as ex:
        result['ok'] = False
        result['error'] = str(ex)
        say(f'FAILED: {ex}')
    s = settings()
    s['applied'] = result
    _save(s)
    exe = os.path.join(install, job['restart']) if job.get('restart') else None
    if exe and os.path.isfile(exe):
        try:
            start([exe], cwd=install, close_fds=True)
        except OSError as ex:
            say(f'restart failed: {ex}')
    log.close()
    return 0 if result['ok'] else 1
