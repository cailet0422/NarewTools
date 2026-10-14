"""NarewModManager deployment engine.

Model
-----
* Installed mods live in Documents\\NarewStudio\\ModManager\\mods\\<id> (narew.modfmt layout).  Up to 2.4.0
  this was %LOCALAPPDATA%\\NarewModManager, which packaged apps see a private copy of (narew.paths.shared_dir);
  adopt_legacy() brings what is there over.
* settings.json keeps the game folder, deploy mode and the ordered mod list
  (first = highest priority) with enabled flags.
* deployed.json records exactly what the last "apply" wrote, so it can be undone:
  patched packs (with the size/mtime of the file we wrote) and loose files we created.

Apply ("pack" mode, default)
-----------------------------
For every pack that receives files, the pristine pack is kept once as ``<pack>.orig`` and the
live pack is rebuilt from that backup plus the winning mod files (unchanged entries are copied
verbatim).  Packs that no longer receive files are restored from their backup.  Mod files whose
path matches no pack become loose files in the game folder (the engine reads files that are not
in any pack from disk); an existing file we did not create is never overwritten.

"loose" mode writes every winning file as a loose file instead (confirmed in game for GUI
textures only, so it is offered as an experimental option).

Table merges (mod format 2)
---------------------------
A mod's ``merge/<table>`` patch adds or replaces single records of a game table (items, bricks,
products, recipes, strings, sky, UI sounds) instead of shipping the whole file, so several mods
can extend the same table.  The base is the highest-priority whole-file replacement if a mod has
one, otherwise the pristine game file; every enabled mod's patch is then applied from the lowest
to the highest priority, so on a record both touch the higher mod wins.

Game files that already exist loose in the game folder (``Language\\string.xml``) are overwritten
only after a one-time ``<file>.orig`` backup, which "restore" puts back.  Executables, libraries
and files in the game folder itself are never overwritten.

If a patched pack no longer matches what we wrote (Steam update or "verify files"), the live
pack is the new original: the stale backup is discarded and the user is asked to apply again.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time

from narew import xmlmerge
from narew.modfmt import ModError, ModFolder, entry_key, extract_zip
from narew.pack import PackError, PackReader, PackWriter, write_atomic
from narew.paths import load_json, manager_home, manager_local_dir, manager_mods_dir, save_json

CLIENT_EXES = ('IsletClient_release(x64).exe', 'IsletClient_release.exe')
# existing game files with these extensions are never replaced by a loose mod file
NEVER_OVERRIDE = {'.exe', '.dll', '.sys', '.bat', '.cmd', '.com', '.scr', '.ps1', '.vbs', '.js', '.msi',
                  '.pack', '.orig', '.ini', '.cfg', '.dat', '.bin'}


def can_override(root: str, target: str) -> bool:
    """Existing loose game data files (in a sub-folder, not code or packs) may be replaced."""
    rel = os.path.relpath(target, root)
    parts = rel.split(os.sep)
    return (len(parts) > 1 and parts[0].lower() != 'data'
            and os.path.splitext(target)[1].lower() not in NEVER_OVERRIDE)


def looks_like_game(root: str) -> bool:
    data = os.path.join(root, 'data')
    return (bool(root) and os.path.isdir(data)
            and any(f.lower().endswith('.pack') for f in os.listdir(data))
            and any(os.path.isfile(os.path.join(root, e)) for e in CLIENT_EXES))


def _sig(path: str) -> list | None:
    try:
        s = os.stat(path)
        return [s.st_size, s.st_mtime_ns]
    except OSError:
        return None


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _content(src: str | bytes) -> bytes:
    if isinstance(src, bytes):
        return src
    with open(src, 'rb') as f:
        return f.read()


class GameBusy(Exception):
    pass


def adopt_legacy(home: str, legacy: str) -> list[str]:
    """Bring over what 2.4.0 and older kept in ``legacy`` (%LOCALAPPDATA%\\NarewModManager); returns the mod ids.

    Runs at every start and only adds: mods ``home`` does not have (each one once, so a mod removed here does
    not come back), the game folder and mode if unset, and the record of what is applied to the game if
    ``home`` has none yet.  A program started from a packaged app sees that app's private copy of ``legacy``,
    so its first start brings those mods over as well."""
    if not os.path.isdir(legacy) or os.path.normcase(os.path.abspath(legacy)) == os.path.normcase(home):
        return []
    path = os.path.join(home, 'settings.json')
    new = load_json(path, {})
    old = load_json(os.path.join(legacy, 'settings.json'), {})
    old_order = [e for e in old.get('mods', []) if isinstance(e, dict) and e.get('id')]
    old_mods = os.path.join(legacy, 'mods')
    names = sorted(os.listdir(old_mods)) if os.path.isdir(old_mods) else []
    listed = [e['id'] for e in old_order]
    names = [n for n in listed if n in names] + [n for n in names if n not in listed]
    enabled = {e['id']: bool(e.get('enabled')) for e in old_order}
    adopted = new.get('adopted', [])
    order = new.get('mods', [])
    got = []
    for mid in names:
        src = os.path.join(old_mods, mid)
        if mid in adopted or not os.path.isfile(os.path.join(src, 'mod.json')):
            continue
        dest = os.path.join(home, 'mods', mid)
        if not os.path.exists(dest):
            shutil.rmtree(dest + '.adopting', ignore_errors=True)
            shutil.copytree(src, dest + '.adopting')
            os.replace(dest + '.adopting', dest)
        adopted.append(mid)
        if not any(e.get('id') == mid for e in order):
            order.append({'id': mid, 'enabled': enabled.get(mid, False)})
        got.append(mid)
    fill = {k: old[k] for k in ('game', 'mode') if old.get(k) and not new.get(k)}
    if got or fill:
        save_json(path, {**new, **fill, 'mods': order, 'adopted': adopted})
    state = load_json(os.path.join(legacy, 'deployed.json'), {})
    if (state.get('packs') or state.get('loose')) and not os.path.exists(os.path.join(home, 'deployed.json')):
        save_json(os.path.join(home, 'deployed.json'), state)
    return got


class Manager:
    def __init__(self):
        self.home = manager_home()
        self.mods_dir = manager_mods_dir()
        self.settings_path = os.path.join(self.home, 'settings.json')
        self.state_path = os.path.join(self.home, 'deployed.json')
        adopt_legacy(self.home, manager_local_dir())
        self.settings = load_json(self.settings_path, {})
        self.settings.setdefault('mods', [])          # [{id, enabled}], first = highest priority
        self.settings.setdefault('mode', 'pack')
        self.state = load_json(self.state_path, {})
        self.log: list[str] = []

    # ------------------------------------------------------------------ persistence
    def reload(self):
        """Re-read settings and deployment state from disk (NarewStudio's "install" or another
        program may have changed them since this Manager was created)."""
        self.settings = load_json(self.settings_path, {})
        self.settings.setdefault('mods', [])
        self.settings.setdefault('mode', 'pack')
        self.state = load_json(self.state_path, {})

    def save_settings(self):
        save_json(self.settings_path, self.settings)

    def save_state(self):
        save_json(self.state_path, self.state)

    @property
    def game_root(self) -> str | None:
        r = self.settings.get('game')
        return r if r and looks_like_game(r) else None

    def set_game(self, root: str):
        root = os.path.abspath(root)
        if not looks_like_game(root):
            raise ModError(f'아일렛 온라인 설치 폴더가 아닙니다: {root}')
        if self.state.get('game') and os.path.normcase(self.state['game']) != os.path.normcase(root) \
                and (self.state.get('packs') or self.state.get('loose')):
            raise ModError('다른 게임 폴더에 적용된 모드가 있습니다. 먼저 "원본 복구"를 하세요.')
        self.settings['game'] = root
        self.save_settings()

    # ------------------------------------------------------------------ mods
    def installed(self) -> list[dict]:
        """Mods in priority order (settings order first, unknown folders appended)."""
        found = {}
        for d in sorted(os.listdir(self.mods_dir)):
            m = ModFolder(os.path.join(self.mods_dir, d))
            if not m.exists():
                continue
            try:
                info = m.read_info()
            except ModError as ex:
                found[d] = {'id': d, 'error': str(ex), 'folder': m}
                continue
            found[info.id] = {'id': info.id, 'info': info, 'folder': m}
        order = []
        seen = set()
        for e in self.settings['mods']:
            if e['id'] in found and e['id'] not in seen:
                order.append({**found[e['id']], 'enabled': bool(e.get('enabled'))})
                seen.add(e['id'])
        for k, v in found.items():
            if k not in seen:
                order.append({**v, 'enabled': False})
        self.settings['mods'] = [{'id': m['id'], 'enabled': m['enabled']} for m in order]
        return order

    def install_zip(self, path: str, replace: bool = False) -> str:
        from narew.modfmt import read_zip_info
        info, _ = read_zip_info(path)
        dest = os.path.join(self.mods_dir, info.id)
        if os.path.exists(dest):
            if not replace:
                raise FileExistsError(info.id)
            shutil.rmtree(dest)
        extract_zip(path, dest)
        self.add_to_order(info.id)
        return info.id

    def install_folder(self, path: str, replace: bool = False) -> str:
        src = ModFolder(path)
        info = src.read_info()
        dest = os.path.join(self.mods_dir, info.id)
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(dest):
            return info.id
        if os.path.exists(dest):
            if not replace:
                raise FileExistsError(info.id)
            shutil.rmtree(dest)
        tmp = dest + '.installing'
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        shutil.copy2(src.info_path, os.path.join(tmp, 'mod.json'))
        pv = os.path.join(src.root, 'preview.png')
        if os.path.isfile(pv):
            shutil.copy2(pv, os.path.join(tmp, 'preview.png'))
        dst = ModFolder(tmp)
        for name, full in src.iter_files():
            t = dst.path_of(name)
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copy2(full, t)
        for name, full in src.iter_merges():
            t = dst.merge_path(name)
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copy2(full, t)
        os.makedirs(os.path.join(tmp, 'files'), exist_ok=True)
        os.replace(tmp, dest)
        self.add_to_order(info.id)
        return info.id

    def add_to_order(self, mod_id: str):
        """A newly installed mod goes on top, switched on (reinstalling keeps its place and switch)."""
        if not any(e['id'] == mod_id for e in self.settings['mods']):
            self.settings['mods'].insert(0, {'id': mod_id, 'enabled': True})
        self.save_settings()

    def remove(self, mod_id: str):
        d = os.path.join(self.mods_dir, mod_id)
        if os.path.isdir(d) and os.path.normcase(os.path.dirname(os.path.abspath(d))) == os.path.normcase(self.mods_dir):
            shutil.rmtree(d)
        self.settings['mods'] = [e for e in self.settings['mods'] if e['id'] != mod_id]
        self.save_settings()

    def set_order(self, entries: list[dict]):
        known = {m['id'] for m in self.installed()}
        self.settings['mods'] = [{'id': e['id'], 'enabled': bool(e.get('enabled'))} for e in entries if e['id'] in known]
        self.installed()
        self.save_settings()

    # ------------------------------------------------------------------ plan
    def winners(self) -> tuple[dict, dict]:
        """entry_key -> (entry name, path, mod id) for the enabled mods; plus conflicts per key."""
        win: dict[str, tuple[str, str, str]] = {}
        providers: dict[str, list[str]] = {}
        for m in self.installed():           # highest priority first
            if not m['enabled'] or 'info' not in m:
                continue
            for name, full in m['folder'].iter_files():
                k = entry_key(name)
                providers.setdefault(k, []).append(m['id'])
                if k not in win:
                    win[k] = (name, full, m['id'])
        conflicts = {k: v for k, v in providers.items() if len(v) > 1}
        return win, conflicts

    def merges(self) -> dict[str, tuple[str, list[tuple[str, str]]]]:
        """entry_key -> (table name, [(mod id, patch path)] lowest priority first) for enabled mods."""
        out: dict[str, tuple[str, list]] = {}
        for m in reversed(self.installed()):
            if not m['enabled'] or 'info' not in m:
                continue
            for name, full in m['folder'].iter_merges():
                out.setdefault(entry_key(name), (name, []))[1].append((m['id'], full))
        return out

    def _merge_base(self, root: str, name: str, key: str, win: dict, index: dict, sources: dict) -> bytes | None:
        """Pristine content a table patch applies to (see module doc)."""
        if key in win:
            with open(win[key][1], 'rb') as f:
                return f.read()
        stem = index.get(key)
        if stem:
            with PackReader(sources[stem]) as r:
                e = r.find(name)
                if e is not None:
                    return r.read(e)
        target = self._loose_target(root, name)
        if target:
            owned = {e['path'] for e in self.state.get('loose', [])}
            backup = target + '.orig'
            # our backup holds the original; a file we wrote without a backup has no original
            src = backup if os.path.isfile(backup) else target if target not in owned else None
            if src and os.path.isfile(src):
                with open(src, 'rb') as f:
                    return f.read()
        return None

    @staticmethod
    def _loose_target(root: str, name: str) -> str | None:
        target = os.path.abspath(os.path.join(root, name.replace('\\', os.sep)))
        return target if target.startswith(os.path.abspath(root) + os.sep) else None

    def pack_index(self, root: str) -> tuple[dict[str, str], dict[str, str]]:
        """(entry_key -> pack stem) from the pristine packs, and stem -> pristine path."""
        data = os.path.join(root, 'data')
        index, sources = {}, {}
        for fn in sorted(os.listdir(data)):
            if not fn.lower().endswith('.pack'):
                continue
            stem = fn[:-5].lower()
            live = os.path.join(data, fn)
            orig = live + '.orig'
            src = orig if os.path.isfile(orig) else live
            sources[stem] = src
            with PackReader(src) as r:
                for e in r:
                    index.setdefault(entry_key(e.name), stem)
        return index, sources

    # ------------------------------------------------------------------ health
    def check(self) -> dict:
        """Detect game files replaced behind our back (game update / file verification)."""
        root = self.game_root
        out = {'updated': [], 'missing_loose': []}
        if not root or not (self.state.get('packs') or self.state.get('loose')):
            return out
        data = os.path.join(root, 'data')
        for stem, rec in list(self.state.get('packs', {}).items()):
            live = os.path.join(data, stem + '.pack')
            if _sig(live) != rec.get('written'):
                out['updated'].append(stem)
                orig = live + '.orig'
                try:
                    with PackReader(live):
                        pass
                    if os.path.isfile(orig):
                        os.remove(orig)      # stale: the live pack is the new original
                except PackError:
                    pass
                del self.state['packs'][stem]
                self.state['stale'] = True
        keep = []
        for e in self.state.get('loose', []):
            p = e['path']
            if e.get('backup') and os.path.isfile(p) and _sha1(p) != e.get('sha1'):
                # the game rewrote a file we had replaced: that is the new original
                try:
                    os.remove(p + '.orig')
                except OSError:
                    pass
                out['updated'].append(os.path.relpath(p, root))
                self.state['stale'] = True
                continue
            keep.append(e)
        if out['updated']:
            self.state['loose'] = keep
            self.save_state()
        return out

    # ------------------------------------------------------------------ apply / restore
    @staticmethod
    def game_running() -> bool:
        if os.name != 'nt':
            return False
        try:
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        low = out.lower()
        return any(f'"{e.lower()}"' in low for e in CLIENT_EXES)

    def _guard(self):
        if self.game_running():
            raise GameBusy('게임이 실행 중입니다. 게임을 종료한 뒤 다시 시도하세요.')

    def _save_packs(self, packs: dict):
        self.state['packs'] = packs
        self.save_state()

    def plan(self, root: str, report: dict) -> tuple[dict, dict, dict]:
        """What apply() writes: ({key: (name, path | bytes)}, pack index, pack sources)."""
        win, conflicts = self.winners()
        index, sources = self.pack_index(root)
        report['conflicts'] = len(conflicts)
        out: dict[str, tuple[str, str | bytes]] = {k: (n, full) for k, (n, full, _) in win.items()}
        for k, (name, patches) in self.merges().items():
            base = self._merge_base(root, name, k, win, index, sources)
            if base is None:
                report['skipped'].append(f'{name} (병합할 원본 파일이 게임에 없음)')
                continue
            texts = []
            for _, path in patches:
                with open(path, encoding='utf-8') as f:
                    texts.append(f.read())
            rep = xmlmerge.MergeReport()
            try:
                merged = xmlmerge.apply_bytes(base, texts, name, rep)
            except (xmlmerge.MergeError, ValueError) as ex:
                report['skipped'].append(f'{name} (병합 실패: {ex})')
                continue
            out[k] = (win[k][0] if k in win else name, merged)
            report['merged'][name] = {'mods': [m for m, _ in patches], 'added': len(rep.added),
                                      'replaced': len(rep.replaced)}
        return out, index, sources

    def apply(self, progress=None) -> dict:
        root = self.game_root
        if not root:
            raise ModError('게임 폴더를 먼저 선택하세요')
        self.check()
        mode = self.settings.get('mode', 'pack')
        report = {'packs': {}, 'restored': [], 'loose': 0, 'skipped': [], 'conflicts': 0, 'merged': {},
                  'overridden': []}
        out, index, sources = self.plan(root, report)
        data = os.path.join(root, 'data')
        per_pack: dict[str, list] = {}
        loose: list[tuple[str, str | bytes]] = []
        for k, (name, src) in out.items():
            stem = index.get(k)
            if stem is None:
                top = name.split('\\', 1)
                cand = top[0].lower() if len(top) > 1 else None
                stem = cand if cand in sources else None
            if mode == 'pack' and stem:
                per_pack.setdefault(stem, []).append((name, src))
            else:
                loose.append((name, src))
        self._guard()
        prev_packs = dict(self.state.get('packs', {}))
        self.state['game'] = root
        steps = len(per_pack) + len(prev_packs) + 1
        done = 0

        def tick(msg):
            nonlocal done
            done += 1
            if progress:
                progress(done, steps, msg)

        # state is saved after every pack so a failure never leaves it out of step with the disk
        cur = dict(prev_packs)
        # 1) restore packs that no longer get files
        for stem in prev_packs:
            if stem not in per_pack:
                self._restore_pack(data, stem)
                cur.pop(stem, None)
                self._save_packs(cur)
                report['restored'].append(stem)
                tick(f'{stem}.pack 원본 복구')
        # 2) rebuild patched packs from their pristine backup
        for stem, files in sorted(per_pack.items()):
            live = os.path.join(data, stem + '.pack')
            orig = live + '.orig'
            if not os.path.isfile(orig):
                shutil.copy2(live, orig + '.tmp')
                os.replace(orig + '.tmp', orig)
            w = PackWriter()
            with PackReader(orig) as r:
                for e in r:
                    w.copy_from(r, e)
                for name, src in files:
                    hit = r.find(name)
                    w.add(hit.name if hit else name, _content(src), hit.attribute if hit else 0)
            write_atomic(live, w)
            cur[stem] = {'written': _sig(live), 'files': len(files), 'orig': _sig(orig)}
            self._save_packs(cur)
            report['packs'][stem] = len(files)
            tick(f'{stem}.pack 패치')
        # 3) loose files
        recorded = {e['path']: e for e in self.state.get('loose', [])}   # files we own
        new_loose = []
        for name, src in loose:
            target = self._loose_target(root, name)
            if not target:
                report['skipped'].append(name)
                continue
            backup = False
            if target in recorded:
                backup = bool(recorded[target].get('backup'))
            elif os.path.exists(target):
                if not os.path.isfile(target) or not can_override(root, target):
                    report['skipped'].append(f'{name} (게임 폴더에 같은 파일이 이미 있음)')
                    continue
                orig = target + '.orig'
                if not os.path.exists(orig):
                    shutil.copy2(target, orig + '.tmp')
                    os.replace(orig + '.tmp', orig)
                backup = True
                report['overridden'].append(name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if isinstance(src, bytes):
                with open(target + '.tmp', 'wb') as f:
                    f.write(src)
            else:
                shutil.copy2(src, target + '.tmp')
            os.replace(target + '.tmp', target)
            e = {'path': target, 'sha1': _sha1(target), 'backup': backup}
            new_loose.append(e)
            recorded[target] = e
            if backup or len(new_loose) % 50 == 0:
                self.state['loose'] = list(recorded.values())
                self.save_state()
        keep = {e['path'] for e in new_loose}
        for p, e in list(recorded.items()):
            if p not in keep:
                self._remove_loose(root, e)
                recorded.pop(p)
        report['loose'] = len(new_loose)
        tick('낱개 파일')
        self.state = {'game': root, 'mode': mode, 'packs': cur, 'loose': new_loose,
                      'mods': [m['id'] for m in self.installed() if m['enabled']],
                      'config': self.config_digest(), 'time': time.time()}
        self.save_state()
        return report

    def _restore_pack(self, data: str, stem: str):
        live = os.path.join(data, stem + '.pack')
        orig = live + '.orig'
        if os.path.isfile(orig):
            os.replace(orig, live)

    def _remove_loose(self, root: str, e: dict):
        p = e['path']
        try:
            if e.get('backup'):
                orig = p + '.orig'
                if os.path.isfile(orig):
                    if os.path.isfile(p) and _sha1(p) != e.get('sha1'):
                        os.remove(orig)          # the game replaced the file since: keep its version
                    else:
                        os.replace(orig, p)
                return
            if os.path.isfile(p) and _sha1(p) == e.get('sha1'):
                os.remove(p)
                d = os.path.dirname(p)
                while os.path.normcase(d) != os.path.normcase(root) and os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    d = os.path.dirname(d)
        except OSError:
            pass

    def restore_all(self) -> dict:
        root = self.state.get('game') or self.game_root
        if not root:
            return {'restored': [], 'loose': 0}
        data = os.path.join(root, 'data')
        packs = list(self.state.get('packs', {}))
        self._guard()
        cur = dict(self.state.get('packs', {}))
        for stem in packs:
            self._restore_pack(data, stem)
            cur.pop(stem, None)
            self._save_packs(cur)
        for e in self.state.get('loose', []):
            self._remove_loose(root, e)
        n = len(self.state.get('loose', []))
        self.state = {}
        self.save_state()
        return {'restored': packs, 'loose': n}

    # ------------------------------------------------------------------ status
    def config_digest(self) -> str:
        """Fingerprint of what would be deployed (enabled mods in order + their files and merges)."""
        h = hashlib.sha1(('2|' + self.settings.get('mode', 'pack')).encode())
        for m in self.installed():
            if not m['enabled'] or 'info' not in m:
                continue
            h.update(m['id'].encode())
            for kind, it in (('f', m['folder'].iter_files()), ('m', m['folder'].iter_merges())):
                for name, full in it:
                    s = os.stat(full)
                    h.update(f'{kind}|{name}|{s.st_size}|{s.st_mtime_ns}'.encode())
        return h.hexdigest()

    def status(self) -> dict:
        applied = bool(self.state.get('packs') or self.state.get('loose'))
        return {
            'applied': applied,
            'in_sync': self.state.get('config') == self.config_digest() if self.state else not any(
                m['enabled'] for m in self.installed()),
            'stale': bool(self.state.get('stale')),
            'time': self.state.get('time'),
            'packs': self.state.get('packs', {}),
            'loose': len(self.state.get('loose', [])),
            'applied_mods': self.state.get('mods', []),
        }
