"""The window's side of the agent link (studio.agent.live): reload what agents change, open what they ask
to show, and publish what the user is looking at (page, world + camera, GUI selection) every 2 seconds."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QTimer

from narew.modfmt import entry_key

from ..agent import live
from .common import guarded, toast


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


class AgentLink(QObject):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.seq = live.last_seq()              # events from before this window started are old news
        self._mtime = None
        self._tick = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(1000)
        self.heartbeat()

    def _on_timer(self):
        self._tick += 1
        self.poll()
        if self._tick % 2 == 0:
            self.heartbeat()

    def stop(self):
        self.timer.stop()
        live.clear_gui_state()

    # ------------------------------------------------------------------ incoming
    @guarded
    def poll(self):
        path = os.path.join(live.live_dir(), 'events.json')
        try:
            m = os.stat(path).st_mtime_ns
        except OSError:
            return
        if m == self._mtime:
            return
        self._mtime = m
        seq, events = live.events_after(self.seq)
        self.seq = max(self.seq, seq)
        names, show = [], None
        for e in events:
            if e.get('pid') == os.getpid():
                continue
            kind = e.get('kind')
            if kind == 'changed':
                names += e.get('names') or []
            elif kind == 'project':
                self._switch_project(e.get('root'))
            elif kind == 'game':
                self._switch_game(e.get('root'))
            elif kind == 'show':
                show = e
        if names:
            self.apply_changes(names)
        if show:
            self.show(show)

    def _leave_ok(self) -> bool:
        cur = self.mw.pages.get(self.mw.current) if self.mw.current else None
        return cur is None or not hasattr(cur, 'can_leave') or cur.can_leave()

    def _switch_project(self, root):
        st = self.mw.st
        if not root or (st.project and _norm(st.project.root) == _norm(root)):
            return
        if not self._leave_ok():
            return
        st.open_project(root)
        self.mw.state_changed()
        toast(f'에이전트가 모드 프로젝트를 열었습니다: {root}')

    def _switch_game(self, root):
        st = self.mw.st
        if not root or (st.game and _norm(st.game.root) == _norm(root)):
            return
        if not self._leave_ok():
            return
        st.open_game(root)
        self.mw.state_changed()

    def apply_changes(self, names: list[str]):
        """Reload pages showing something an agent changed (never over unsaved edits in the window)."""
        mw, st = self.mw, self.mw.st
        st.invalidate()
        keys = {entry_key(n) for n in names}
        paths = {_norm(n) for n in names if os.path.isabs(n)}
        mw.refresh_header()
        kept = []
        for key, page in mw.pages.items():
            visible = page.isVisible()
            if key == 'world':
                doc = page.doc
                hit = doc is not None and (entry_key(doc.name) in keys or (doc.path and _norm(doc.path) in paths))
                if hit:
                    if page.view.dirty():
                        kept.append('월드')
                    else:
                        page.reload_doc()
                if visible:
                    page.items = page.svc.list()
                    page.load_saved()
            elif key == 'gui':
                if page.name and entry_key(page.name) in keys:
                    if page.text != page.saved_text:
                        kept.append('GUI')
                    else:
                        page.open_layout(page.name)
                if visible:
                    page.fill()
            elif key == 'resources':
                ed = page.editor
                if ed.name and entry_key(ed.name) in keys:
                    if ed.code and ed.dirty:
                        kept.append('리소스')
                    else:
                        ed.open(ed.name)
            elif key == 'sound':
                if page.name and entry_key(page.name) in keys:
                    if page.dirty():
                        kept.append('사운드')
                    else:
                        page.open_sound(page.name)
                if visible:
                    page.fill()
            elif key in ('blocks', 'create', 'project', 'explorer') and visible and hasattr(page, 'on_state'):
                guarded(page.on_state)()
        msg = f'에이전트가 바꾼 것 {len(names)}개를 반영했습니다'
        if kept:
            msg += f" (저장 안 한 편집이 있어 그대로 둔 화면: {', '.join(kept)})"
        toast(msg, 8000)

    def show(self, e: dict):
        page = e.get('page')
        if not page:
            return
        if page == 'world' and e.get('path'):
            self.mw.open_page('world', name=('file', e['path']))
        elif e.get('name'):
            self.mw.open_page(page, name=e['name'])
        else:
            self.mw.open_page(page)
        self.mw.raise_()
        self.mw.activateWindow()

    # ------------------------------------------------------------------ outgoing
    def heartbeat(self):
        mw, st = self.mw, self.mw.st
        state = {'page': mw.current, 'game': st.game.root if st.game else None,
                 'project': st.project.root if st.project else None, 'open': {}}
        try:
            wp = mw.pages.get('world')
            if wp is not None and wp.doc is not None:
                v = wp.view
                t = v.cam.target
                cam = [round(float(t[0]), 1), round(float(t[1]), 1), round(float(-t[2]), 1)]
                state['open']['world'] = {
                    'name': None if wp.doc.path else wp.doc.name, 'path': wp.doc.path,
                    'kind': 'world' if wp.doc.is_world else 'prefab', 'dirty': v.dirty(),
                    'camera_target_xyz': cam, 'mouse_cell_xyz': list(v.hover) if v.hover else None,
                    'selected_brick': v.selected, 'tool': v.tool}
                state['camera'] = cam
            gp = mw.pages.get('gui')
            if gp is not None and gp.name:
                sel = gp.selected_indices() if gp.root is not None else []
                state['open']['gui'] = {'name': gp.name, 'selected_nodes': sel, 'dirty': gp.text != gp.saved_text}
                state['selection'] = sel
            rp = mw.pages.get('resources')
            if rp is not None and rp.editor.name:
                state['open']['resource'] = rp.editor.name
            sp = mw.pages.get('sound')
            if sp is not None and sp.name:
                state['open']['sound'] = sp.name
        except Exception as ex:                 # a half-built page must not stop the heartbeat
            state['error'] = str(ex)
        live.write_gui_state(state)
