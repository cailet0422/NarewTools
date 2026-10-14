"""State of one agent process: the studio (game + project), open world sessions, clipboards."""
from __future__ import annotations

import os
import time

from narew.paths import appdata, load_json, shared_dir

from ..core import Studio, StudioError
from . import live


class Clip:
    """Copied bricks [y, z, x] and the products anchored in them (offsets from the low corner)."""

    def __init__(self, arr, products, source: str):
        self.arr = arr
        self.products = products             # [(type, dx, dy, dz, rot, state)]
        self.source = source

    @property
    def size(self):
        h, d, w = self.arr.shape
        return [w, h, d]


class AgentContext:
    def __init__(self, game: str | None = None, project: str | None = None, follow: bool = True):
        self.st = Studio()
        self.pinned = bool(game or project)
        if self.pinned:                      # explicit folders: never change the window's settings
            self.st.save_settings = lambda: None
            if game:
                self.st.open_game(game)
            elif self.st.settings.get('game'):
                try:
                    self.st.open_game(self.st.settings['game'])
                except StudioError:
                    pass
            if project:
                self.st.open_project(project)
        else:
            self.st.restore()
        self.follow = follow and not self.pinned
        self._world = self._creator = self._models = None
        self.sessions: dict = {}             # id -> EditSession
        self.session_meta: dict = {}         # id -> {'project': root, 'source': ...}
        self.clips: dict[str, Clip] = {}
        self._n_session = self._n_clip = 0
        self._gl = None
        self._cache: dict = {}
        self.out_dir = os.path.join(appdata('NarewStudio'), 'agent', 'out')      # pictures for the agent only
        os.makedirs(self.out_dir, exist_ok=True)
        self._clean_out()

    # ------------------------------------------------------------------ services
    @property
    def world(self):
        if self._world is None:
            from ..svc_world import WorldService
            self._world = WorldService(self.st)
        return self._world

    @property
    def creator(self):
        if self._creator is None:
            from ..creator import Creator
            self._creator = Creator(self.st)
        return self._creator

    @property
    def models(self):
        if self._models is None:
            from ..svc_models import ModelService
            self._models = ModelService(self.st)
        return self._models

    def gl(self):
        """A hidden OpenGL 3.3 context for offscreen renders (created on first use)."""
        if self._gl is None:
            import moderngl
            try:
                self._gl = moderngl.create_standalone_context(require=330)
            except Exception as ex:
                raise StudioError(f'OpenGL 3.3을 쓸 수 없어 3D 그림을 만들 수 없습니다: {ex}. view="top"을 쓰세요.')
        return self._gl

    # ------------------------------------------------------------------ per call
    def before_call(self, t):
        """Pick up what the window (or another agent) changed since the last call."""
        st = self.st
        if self.follow:
            s = load_json(st.settings_path, {})
            g, p = s.get('game'), s.get('project')
            if g and (not st.game or os.path.normcase(os.path.abspath(g)) != os.path.normcase(st.game.root)):
                try:
                    st.open_game(g)
                    self._cache.clear()
                except StudioError:
                    pass
            if p and (not st.project or os.path.normcase(os.path.abspath(p)) != os.path.normcase(st.project.root)):
                try:
                    st.open_project(p)
                except StudioError:
                    pass
            elif not p and st.project and s:
                st.close_project()
        st.invalidate()

    def notify(self, names=(), **extra):
        """Tell a running NarewStudio window what changed so it reloads it."""
        names = [n for n in names if n]
        if names:
            live.post('changed', names=names, project=self.st.project.root if self.st.project else None, **extra)

    def project_root(self) -> str | None:
        return self.st.project.root if self.st.project else None

    # ------------------------------------------------------------------ sessions
    def new_session_id(self) -> str:
        self._n_session += 1
        return f'w{self._n_session}'

    def session(self, sid: str):
        s = self.sessions.get(sid)
        if s is None:
            have = ', '.join(self.sessions) or '없음'
            raise StudioError(f'열린 월드 세션 {sid!r}이(가) 없습니다 (열린 세션: {have}). world_open으로 여세요.')
        return s

    def new_clip(self, clip: Clip) -> str:
        self._n_clip += 1
        cid = f'c{self._n_clip}'
        self.clips[cid] = clip
        return cid

    # ------------------------------------------------------------------ files
    def out_path(self, stem: str, ext: str = '.png') -> str:
        safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in stem)[:80]
        self._n_out = getattr(self, '_n_out', 0) + 1          # several pictures in the same second
        return os.path.join(self.out_dir,
                            f'{safe}_{time.strftime("%H%M%S")}_{os.getpid() % 1000:03d}_{self._n_out}{ext}')

    def _clean_out(self):
        cutoff = time.time() - 2 * 86400
        try:
            for fn in os.listdir(self.out_dir):
                p = os.path.join(self.out_dir, fn)
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except OSError:
            pass

    def cached(self, key, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    @staticmethod
    def settings_dir() -> str:
        return shared_dir()
