"""The game's own world saves, and copying the current one to a .bwr file.

When you play a world on your own PC (single play, or a server you host) the game's local server
keeps it in ``<game>\\Local\\<world>.bwr`` (next to ``.lnd``/``.npc``/``.db``) and rewrites that file
while you play.  ``<game>\\backup_map\\map_<month>_<day>_<hour>.bwr`` are dated copies the game makes.
These files use the same .bwr format as the maps in ``map.pack`` (docs/05).

Worlds on servers other people host are not on this PC; they are not handled here.
"""
from __future__ import annotations

import json
import os
import shutil
import time

from narew import brick
from narew.paths import documents_dir

from .core import StudioError

SAVE_DIRS = (('Local', '현재 월드'), ('backup_map', '게임 백업'))


def default_capture_dir() -> str:
    return os.path.join(documents_dir(), 'NarewStudio', 'Worlds')


def list_saved(game_root: str) -> list[dict]:
    """Saved worlds, the game's current world(s) first, newest first within each folder."""
    out = []
    for folder, label in SAVE_DIRS:
        d = os.path.join(game_root, folder)
        if not os.path.isdir(d):
            continue
        rows = []
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            if fn.lower().endswith('.bwr') and os.path.isfile(p):
                s = os.stat(p)
                rows.append({'path': p, 'name': fn, 'folder': folder, 'label': label,
                             'mtime': s.st_mtime, 'size': s.st_size})
        out += sorted(rows, key=lambda r: -r['mtime'])
    return out


def list_captured(dest_dir: str | None = None) -> list[dict]:
    """.bwr files in the capture folder (copies made by capture() or saved from the editor), newest first."""
    d = dest_dir or default_capture_dir()
    if not os.path.isdir(d):
        return []
    rows = []
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        if fn.lower().endswith('.bwr') and os.path.isfile(p):
            s = os.stat(p)
            rows.append({'path': p, 'name': fn, 'folder': d, 'label': '캡처', 'mtime': s.st_mtime, 'size': s.st_size})
    return sorted(rows, key=lambda r: -r['mtime'])


CLIENT_EXES = ('IsletClient_release(x64).exe', 'IsletClient_release.exe')


def is_game_save(game_root: str | None, path: str) -> bool:
    """True for a file inside a game's save folders (never written by the tools): the configured
    game's Local\\ / backup_map\\, or those folders of any Islet Online install (next to the client exe)."""
    p = os.path.abspath(path)
    if game_root:
        pn = os.path.normcase(p)
        if any(pn.startswith(os.path.normcase(os.path.join(game_root, f)) + os.sep) for f, _ in SAVE_DIRS):
            return True
    return save_game_root(p) is not None


def save_game_root(path: str) -> str | None:
    """The Islet Online install whose Local\\ / backup_map\\ holds path (next to the client exe), or None."""
    d = os.path.dirname(os.path.abspath(path))
    while os.path.basename(d) and os.path.basename(d).lower() not in {f.lower() for f, _ in SAVE_DIRS}:
        d = os.path.dirname(d) if os.path.dirname(d) != d else ''
    root = os.path.dirname(d) if d else None
    return root if root and any(os.path.isfile(os.path.join(root, e)) for e in CLIENT_EXES) else None


def read_stable(path: str, tries: int = 8, wait: float = 0.5) -> bytes:
    """Read a save the game may be writing right now: retry until the file is unchanged during
    the read and parses as a complete world."""
    last = None
    for i in range(tries):
        try:
            a = os.stat(path)
            with open(path, 'rb') as f:
                data = f.read()
            b = os.stat(path)
            if (a.st_size, a.st_mtime_ns) == (b.st_size, b.st_mtime_ns) and len(data) == b.st_size:
                brick.World.from_bytes(data)
                return data
            last = '저장 중'
        except (OSError, brick.BrickError) as ex:
            last = ex
        if i + 1 < tries:
            time.sleep(wait)
    raise StudioError(f'월드 저장본을 읽을 수 없습니다 (게임이 저장하는 중일 수 있습니다): {last}')


def current_world(game_root: str) -> dict | None:
    """The world the game saved most recently in Local\\ (= the one being played)."""
    cur = [s for s in list_saved(game_root) if s['folder'] == 'Local']
    return cur[0] if cur else None


COMPANIONS = ('.lnd', '.npc')     # IBrickServer::Save writes .npc, then .lnd, then .bwr last


def capture(game_root: str, dest_dir: str | None = None) -> dict:
    """Copy the current world to dest_dir as <world>_<date>_<time of the game's save>.bwr, with the
    .lnd/.npc the game saved alongside (same name) so the copy is a complete backup.
    A second capture of the same save returns the existing file instead of a duplicate."""
    cur = current_world(game_root)
    if not cur:
        raise StudioError('게임 폴더의 Local\\에 월드 저장본이 없습니다. 싱글 플레이(내 월드)로 한 번 들어갔다 나오세요.')
    data = read_stable(cur['path'])          # .bwr is written last, so the others are complete by now
    saved_at = os.stat(cur['path']).st_mtime
    extra = {}
    for ext in COMPANIONS:
        p = cur['path'][:-4] + ext
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                extra[ext] = f.read()
    dest_dir = dest_dir or default_capture_dir()
    os.makedirs(dest_dir, exist_ok=True)
    stem = os.path.splitext(cur['name'])[0]
    out = os.path.join(dest_dir, f"{stem}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(saved_at))}.bwr")
    new = True
    if os.path.isfile(out):
        with open(out, 'rb') as f:
            new = f.read() != data
        if new:                       # same second, different content: keep both
            out = out[:-4] + f"_{time.strftime('%H%M%S')}.bwr"
    if new:
        for ext, blob in extra.items():
            with open(out[:-4] + ext + '.tmp', 'wb') as f:
                f.write(blob)
            os.replace(out[:-4] + ext + '.tmp', out[:-4] + ext)
        with open(out + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(out + '.tmp', out)
    return {'path': out, 'world': cur['name'], 'saved_at': saved_at, 'size': len(data), 'new': new,
            'companions': [out[:-4] + ext for ext in extra]}


# --------------------------------------------------------------------------- putting a world back

SAVE_FILES = ('.bwr', '.lnd', '.npc', '.db')     # what the game keeps per world in Local\


def backup_root() -> str:
    """Outside the game folder: Local\\ is synced by Steam Cloud and must not fill up with copies."""
    return os.path.join(documents_dir(), 'NarewStudio', 'WorldBackups')


def game_running() -> bool:
    from modmanager.core import Manager
    return Manager.game_running()


def local_worlds(game_root: str) -> list[dict]:
    return [s for s in list_saved(game_root) if s['folder'] == 'Local']


def check_apply(game_root: str, src_path: str, world: str | None = None) -> dict:
    """What apply_to_game would do: target save, sizes, and whether the game saved after src."""
    targets = local_worlds(game_root)
    if world:
        targets = [t for t in targets if t['name'].lower() == world.lower()]
    if not targets:
        raise StudioError('게임 폴더의 Local\\에 넣을 월드 저장본이 없습니다. 싱글 플레이로 한 번 들어갔다 나오세요.')
    t = targets[0]
    new = brick.World.from_bytes(read_stable(src_path))
    old = brick.World.from_bytes(read_stable(t['path']))
    if (new.width, new.depth) != (old.width, old.depth):
        raise StudioError(f"크기가 다른 월드는 넣을 수 없습니다 ({new.width}×{new.depth} → {old.width}×{old.depth}). "
                          '같은 월드의 저장본을 고친 파일을 쓰세요.')
    src_mtime = os.stat(src_path).st_mtime
    return {'target': t['path'], 'world': t['name'], 'size': (old.width, old.depth),
            'save_time': t['mtime'], 'src_time': src_mtime, 'save_newer': t['mtime'] > src_mtime + 1}


def _backup(target: str, reason: str) -> str:
    stem = os.path.splitext(os.path.basename(target))[0]
    base = os.path.join(backup_root(), f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}")
    d, n = base, 2
    while os.path.exists(d):
        d, n = f'{base}_{n}', n + 1
    os.makedirs(d)
    for ext in SAVE_FILES:
        p = target[:-4] + ext
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(d, stem + ext))
    with open(os.path.join(d, 'backup.json'), 'w', encoding='utf-8') as f:
        json.dump({'world': stem, 'from': os.path.dirname(target), 'reason': reason,
                   'time': time.time()}, f, ensure_ascii=False, indent=1)
    return d


def apply_to_game(game_root: str, src_path: str, world: str | None = None) -> dict:
    """Replace the game's Local\\<world>.bwr with src_path, after backing up the save
    (.bwr/.lnd/.npc/.db) to backup_root().  The game must not be running."""
    if game_running():
        raise StudioError('게임이 실행 중입니다. 게임을 완전히 끈 뒤 다시 하세요 (켜져 있으면 게임이 다시 덮어씁니다).')
    info = check_apply(game_root, src_path, world)
    data = read_stable(src_path)
    target = info['target']
    bdir = _backup(target, f'내 월드에 적용 전 ({os.path.basename(src_path)})')
    tmp = target + '.narew-tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, target)
    return {**info, 'backup': bdir, 'bytes': len(data)}


def list_backups(world: str | None = None) -> list[dict]:
    out = []
    root = backup_root()
    if not os.path.isdir(root):
        return out
    for d in os.listdir(root):
        full = os.path.join(root, d)
        meta = os.path.join(full, 'backup.json')
        if not os.path.isfile(meta):
            continue
        try:
            with open(meta, encoding='utf-8') as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if world and m.get('world', '').lower() != os.path.splitext(world)[0].lower():
            continue
        out.append({'dir': full, **m, 'files': sorted(os.listdir(full))})
    return sorted(out, key=lambda b: -b.get('time', 0))


def restore_backup(game_root: str, bdir: str) -> dict:
    """Put a backup back into Local\\ (the current save is backed up first)."""
    if game_running():
        raise StudioError('게임이 실행 중입니다. 게임을 완전히 끈 뒤 다시 하세요.')
    with open(os.path.join(bdir, 'backup.json'), encoding='utf-8') as f:
        world = json.load(f)['world']
    target = os.path.join(game_root, 'Local', world + '.bwr')
    before = _backup(target, '백업 되돌리기 전') if os.path.isfile(target) else None
    restored = []
    for ext in SAVE_FILES:
        src = os.path.join(bdir, world + ext)
        if os.path.isfile(src):
            dst = target[:-4] + ext
            shutil.copy2(src, dst + '.narew-tmp')
            os.replace(dst + '.narew-tmp', dst)
            restored.append(os.path.basename(dst))
    return {'world': world, 'restored': restored, 'backup_of_current': before}
