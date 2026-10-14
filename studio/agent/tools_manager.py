"""NarewModManager from the agent: install the project, enable/order mods, apply to the game / restore."""
from __future__ import annotations

from .. import svc_resources as R
from ..core import StudioError
from .registry import BOOL, INT, STR, tool


def _mgr():
    from modmanager.core import Manager
    m = Manager()
    m.reload()
    return m


def _mods(m) -> list[dict]:
    out = []
    for i, e in enumerate(m.installed()):
        row = {'priority': i + 1, 'id': e['id'], 'enabled': e['enabled']}
        if 'info' in e:
            row.update(name=e['info'].name, version=e['info'].version)
        else:
            row['error'] = e.get('error')
        out.append(row)
    return out


@tool('manager_status', 'manager',
      'Mod manager: installed mods in priority order (1 = wins conflicts), enabled flags, the game folder it '
      'applies to, and whether what is applied matches the current setup.')
def manager_status(ctx):
    m = _mgr()
    st = m.status()
    return {'game': m.game_root, 'mode': m.settings.get('mode', 'pack'), 'mods': _mods(m),
            'applied': st['applied'], 'in_sync': st['in_sync'], 'stale': st['stale'],
            'applied_mods': st['applied_mods'], 'patched_packs': sorted(st['packs']), 'loose_files': st['loose'],
            'patched_dlls': st['binaries']}


@tool('manager_install_project', 'manager',
      'Install (or update) the open project into the mod manager, enabled and on top. Does not touch the game.',
      writes=True, replace=BOOL('Replace an installed copy with the same id.', True))
def manager_install_project(ctx, replace):
    dest = R.install_to_manager(ctx.st, replace)
    if dest is None:
        raise StudioError('이미 설치돼 있습니다 (replace=true로 덮어쓰기)')
    return {'installed': dest, 'next': 'manager_apply (ask the user first) puts enabled mods into the game'}


@tool('manager_set_mod', 'manager', 'Enable/disable an installed mod and/or move it to a priority (1 = top).',
      writes=True, id=STR('Mod id.'), enabled=BOOL('On or off.', None), priority=INT('New position (1 = top).', None))
def manager_set_mod(ctx, id, enabled, priority):
    m = _mgr()
    rows = [{'id': e['id'], 'enabled': e['enabled']} for e in m.installed()]
    i = next((k for k, r in enumerate(rows) if r['id'] == id), None)
    if i is None:
        raise StudioError(f'설치되지 않은 모드: {id}')
    if enabled is not None:
        rows[i]['enabled'] = enabled
    if priority is not None:
        r = rows.pop(i)
        rows.insert(max(0, min(len(rows), priority - 1)), r)
    m.set_order(rows)
    return {'mods': _mods(m)}


@tool('manager_apply', 'manager',
      'Apply the enabled mods to the game install (packs rebuilt from .orig backups, tables merged, engine DLL code '
      'patches checked and written after a .orig backup). The game must be closed. Without confirm=true it only lists '
      'what would change. Ask the user before confirming.', game=True,
      confirm=BOOL('Really write to the game.', False))
def manager_apply(ctx, confirm):
    m = _mgr()
    if not m.game_root:
        raise StudioError('모드 매니저에 게임 폴더가 없습니다. NarewModManager에서 먼저 고르세요.')
    if not confirm:
        win, conflicts = m.winners()
        merges = m.merges()
        packs = {}
        index, sources = m.pack_index(m.game_root)
        for k, (name, _, mod) in win.items():
            stem = index.get(k)
            if stem is None:                # a new file goes into the pack named by its top folder (as apply)
                top = name.split('\\', 1)
                stem = top[0].lower() if len(top) > 1 and top[0].lower() in sources else 'loose/new'
            packs.setdefault(stem, []).append(name)
        rep = {'skipped': []}
        bins = m.plan_binaries(m.game_root, rep)
        return {'dry_run': True, 'game': m.game_root, 'enabled_mods': [r['id'] for r in _mods(m) if r['enabled']],
                'files_by_pack': {p: (len(v), v[:20]) for p, v in packs.items()},
                'table_merges': {v[0]: [mod for mod, _ in v[1]] for v in merges.values()},
                'code_patches': {f: used for f, (_, used) in bins.items()}, 'code_patches_skipped': rep['skipped'],
                'conflicts': {k: v for k, v in list(conflicts.items())[:30]}, 'game_running': m.game_running(),
                'next': 'ask the user, then call again with confirm=true'}
    r = m.apply()
    return {'applied': True, **{k: v for k, v in r.items() if k != 'packs'}, 'packs': sorted(r.get('packs', {}))}


@tool('manager_restore', 'manager',
      'Undo everything the mod manager applied (packs and files back to the originals). Game must be closed. '
      'Ask the user first.', game=True, confirm=BOOL('Really write to the game.', False))
def manager_restore(ctx, confirm):
    m = _mgr()
    if not confirm:
        return {'dry_run': True, **m.status(), 'next': 'ask the user, then call again with confirm=true'}
    return m.restore_all()
