"""Link between agent processes and a running NarewStudio window.

Two small files in Documents\\NarewStudio\\agent\\ (no sockets, nothing to configure; not in AppData, of which
an agent started from a packaged app such as the Claude desktop app sees a private copy - narew.paths.shared_dir):

* events.json - agents append what they changed (resource names, world files, the project they
  switched to) and requests to show something; the window polls it and reloads / opens pages.
* gui.json    - the window writes its state every 2 s: current page and what it has open (world,
  camera position, GUI layout, selection), so an agent can ask "what is the user looking at".
"""
from __future__ import annotations

import os
import time

from narew.paths import load_json, save_json, shared_dir

KEEP = 200
GUI_STALE = 8.0            # seconds without a heartbeat = window closed


def live_dir() -> str:
    return shared_dir('agent')


def _events_path() -> str:
    return os.path.join(live_dir(), 'events.json')


def post(kind: str, **data) -> int:
    """Append an event: kind 'changed' (names=[...]), 'project' (root=...), 'game' (root=...),
    'show' (page=..., name=..., path=...)."""
    path = _events_path()
    cur = load_json(path, {})
    seq = int(cur.get('seq', 0)) + 1
    ev = {'seq': seq, 'time': time.time(), 'pid': os.getpid(), 'kind': kind, **data}
    events = (cur.get('events') or [])[-(KEEP - 1):] + [ev]
    try:
        save_json(path, {'seq': seq, 'events': events})
    except OSError:
        pass                    # the window is reading it right now; the next post carries on
    return seq


def last_seq() -> int:
    return int(load_json(_events_path(), {}).get('seq', 0))


def events_after(seq: int) -> tuple[int, list[dict]]:
    cur = load_json(_events_path(), {})
    return int(cur.get('seq', 0)), [e for e in cur.get('events') or [] if e.get('seq', 0) > seq]


def write_gui_state(state: dict):
    try:
        save_json(os.path.join(live_dir(), 'gui.json'), {**state, 'pid': os.getpid(), 'time': time.time()})
    except OSError:
        pass


def clear_gui_state():
    """The window is closing."""
    try:
        os.remove(os.path.join(live_dir(), 'gui.json'))
    except OSError:
        pass


def gui_state() -> dict | None:
    """The window's last heartbeat, or None when no NarewStudio window is running."""
    s = load_json(os.path.join(live_dir(), 'gui.json'), None)
    if not s or time.time() - float(s.get('time', 0)) > GUI_STALE:
        return None
    return s
