"""Well-known per-user locations shared by NarewStudio and NarewModManager."""
from __future__ import annotations

import json
import os


def appdata(app: str) -> str:
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    p = os.path.join(base, app)
    os.makedirs(p, exist_ok=True)
    return p


def manager_home() -> str:
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or os.path.expanduser('~')
    p = os.path.join(base, 'NarewModManager')
    os.makedirs(p, exist_ok=True)
    return p


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
