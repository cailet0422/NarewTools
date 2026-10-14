"""Well-known per-user locations shared by NarewStudio and NarewModManager."""
from __future__ import annotations

import json
import os


def appdata(app: str) -> str:
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    p = os.path.join(base, app)
    os.makedirs(p, exist_ok=True)
    return p


def shared_dir(*parts: str) -> str:
    """Folder for what NarewStudio, NarewModManager and AI agents must all see: Documents\\NarewStudio.

    Not AppData: Windows gives packaged (MSIX) programs - the Claude desktop app, Python from the Microsoft
    Store - a private copy of every file they create under AppData, so an agent started from one of them
    installed mods and changed settings that the user's own NarewStudio and NarewModManager never saw."""
    p = os.path.join(documents_dir(), 'NarewStudio', *parts)
    os.makedirs(p, exist_ok=True)
    return p


def manager_home() -> str:
    """Installed mods, their order and the record of what is applied to the game."""
    return shared_dir('ModManager')


def manager_local_dir() -> str:
    """%LOCALAPPDATA%\\NarewModManager: the mod manager's log; up to 2.4.0 also everything in manager_home()."""
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'NarewModManager')


def manager_mods_dir() -> str:
    """Installed mods live here; NarewStudio installs projects into it directly."""
    p = os.path.join(manager_home(), 'mods')
    os.makedirs(p, exist_ok=True)
    return p


def documents_dir() -> str:
    home = os.path.expanduser('~')
    for cand in (os.path.join(home, 'Documents'), os.path.join(home, 'OneDrive', 'Documents'), home):
        if os.path.isdir(cand):
            return cand
    return home


def load_json(path: str, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
