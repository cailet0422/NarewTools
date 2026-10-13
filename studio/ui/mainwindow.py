"""NarewStudio main window: header, navigation, lazily created pages, status/progress."""
from __future__ import annotations

import importlib

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from narew.modfmt import ModInfo, slug

from ..core import Studio, StudioError
from .common import button, error, guarded, hbox, label, pick_folder, toast

PAGES = [
    ('resources', '🗂  리소스 편집', 'page_resources', 'ResourcesPage'),
    ('blocks', '🧱  블록 텍스처', 'page_blocks', 'BlocksPage'),
    ('furniture', '🪑  가구 텍스처', 'page_furniture', 'FurniturePage'),
    ('create', '✨  새 리소스 추가', 'page_create', 'CreatePage'),
    ('gui', '🖼  GUI 편집', 'page_gui', 'GuiPage'),
    ('sound', '🎚  사운드 편집', 'page_sound', 'SoundPage'),
    ('-', None, None, None),
    ('world', '🌍  월드 에디터', 'page_world', 'WorldPage'),
    ('models', '🧍  모델 뷰어', 'page_models', 'ModelsPage'),
    ('-', None, None, None),
    ('explorer', '📦  팩 탐색기', 'page_explorer', 'ExplorerPage'),
    ('project', '⭐  내 모드', 'page_project', 'ProjectPage'),
]


class NewProjectDialog(QDialog):
    def __init__(self, st: Studio, parent=None):
        super().__init__(parent)
        self.setWindowTitle('새 모드 만들기')
        self.setMinimumWidth(520)
        f = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText('예: 파스텔 블록 팩')
        self.author = QLineEdit()
        self.version = QLineEdit('1.0.0')
        self.desc = QTextEdit()
        self.desc.setFixedHeight(70)
        self.dir = QLineEdit(st.default_projects_dir())
        browse = button('…', lambda: self.dir.setText(pick_folder('프로젝트를 만들 폴더', self.dir.text()) or self.dir.text()))
        f.addRow('모드 이름', self.name)
        f.addRow('만든 사람', self.author)
        f.addRow('버전', self.version)
        f.addRow('설명', self.desc)
        f.addRow('저장 위치', hbox(self.dir, browse))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText('만들기')
        bb.button(QDialogButtonBox.Cancel).setText('취소')
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def info(self) -> ModInfo:
        name = self.name.text().strip()
        if not name:
            raise StudioError('모드 이름을 입력하세요')
        return ModInfo(id=slug(name), name=name, version=self.version.text().strip() or '1.0.0',
                       author=self.author.text().strip(), description=self.desc.toPlainText().strip())


class MainWindow(QMainWindow):
    def __init__(self, st: Studio):
        super().__init__()
        self.st = st
        self.setObjectName('main')
        self.setWindowTitle('NarewStudio — 아일렛 온라인 모딩 툴')
        self.resize(1480, 920)
        self.pages: dict[str, QWidget] = {}
        self.current = None
        self._busy: dict[str, int] = {}
        self.capture_win = None
        # header
        head = QFrame()
        head.setObjectName('head')
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 6, 12, 6)
        logo = QLabel('<b>Narew<span style="color:#5fb36b">Studio</span></b>')
        logo.setStyleSheet('font-size: 13pt;')
        self.game_btn = QPushButton()
        self.game_btn.setToolTip('게임 폴더 (클릭해서 바꾸기)')
        self.game_btn.clicked.connect(guarded(self.choose_game))
        self.proj_btn = QPushButton()
        self.proj_btn.setToolTip('모드 프로젝트 (클릭하면 "내 모드")')
        self.proj_btn.clicked.connect(lambda: self.open_page('project'))
        hl.addWidget(logo)
        hl.addSpacing(10)
        hl.addWidget(label('게임', faint=True))
        hl.addWidget(self.game_btn)
        hl.addWidget(label('모드', faint=True))
        hl.addWidget(self.proj_btn)
        hl.addStretch(1)
        hl.addWidget(button('새 모드', self.new_project))
        hl.addWidget(button('모드 열기', self.open_project))
        # nav + stack
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.setFixedWidth(190)
        self.nav.setIconSize(QSize(0, 0))
        self.keys = []
        for key, text, _, _ in PAGES:
            if key == '-':
                it = QListWidgetItem('')
                it.setFlags(Qt.NoItemFlags)
                it.setSizeHint(QSize(10, 10))
                self.nav.addItem(it)
                self.keys.append(None)
                continue
            self.nav.addItem(QListWidgetItem(text))
            self.keys.append(key)
        self.nav.currentRowChanged.connect(self._nav_changed)
        self.stack = QStackedWidget()
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self.nav)
        bl.addWidget(self.stack, 1)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(head)
        cl.addWidget(body, 1)
        self.setCentralWidget(central)
        # status
        self.busy_label = label('', muted=True)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.refresh_header()
        from .agentlink import AgentLink        # AI agents (studio.agent) editing the same project
        self.agent_link = AgentLink(self)

    # ------------------------------------------------------------------ pages
    def _nav_changed(self, row: int):
        key = self.keys[row] if 0 <= row < len(self.keys) else None
        if key:
            self.open_page(key)

    def page(self, key: str):
        if key not in self.pages:
            spec = next(p for p in PAGES if p[0] == key)
            mod = importlib.import_module(f'studio.ui.{spec[2]}')
            w = getattr(mod, spec[3])(self.st, self)
            self.pages[key] = w
            self.stack.addWidget(w)
        return self.pages[key]

    def open_page(self, key: str, **kw):
        if self.current and self.current != key:
            cur = self.pages.get(self.current)
            if cur is not None and hasattr(cur, 'can_leave') and not cur.can_leave():
                self.nav.blockSignals(True)
                self.nav.setCurrentRow(self.keys.index(self.current))
                self.nav.blockSignals(False)
                return
        try:
            w = self.page(key)
        except Exception as ex:
            error(f'화면을 열 수 없습니다: {ex}')
            raise
        self.stack.setCurrentWidget(w)
        self.current = key
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(self.keys.index(key))
        self.nav.blockSignals(False)
        self.st.settings['last_page'] = key
        if hasattr(w, 'on_show'):
            guarded(w.on_show)(**kw)

    def state_changed(self):
        """Game or project replaced: tell every created page."""
        self.refresh_header()
        for w in self.pages.values():
            if hasattr(w, 'on_state'):
                guarded(w.on_state)()

    # ------------------------------------------------------------------ header
    def refresh_header(self):
        st = self.st
        self.game_btn.setText(st.game.root if st.game else '선택 안 됨 (클릭)')
        if st.project:
            try:
                info = st.project.read_info()
                n = len(st.project_files()) + len(st.project_merges())
                self.proj_btn.setText(f'{info.name} · 바뀐 것 {n}개')
            except Exception:
                self.proj_btn.setText(st.project.root)
        else:
            self.proj_btn.setText('없음 (새 모드를 만드세요)')

    def busy(self, msg: str, on: bool):
        self._busy[msg] = self._busy.get(msg, 0) + (1 if on else -1)
        if self._busy[msg] <= 0:
            self._busy.pop(msg)
        self.busy_label.setText(' · '.join(f'⏳ {m}' for m in self._busy))
        if not self._busy:
            self.progress.hide()

    def show_progress(self, done: int, total: int, msg: str = ''):
        self.progress.show()
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(f'{done:,}/{total:,}')
        if msg:
            self.statusBar().showMessage(msg, 2000)

    # ------------------------------------------------------------------ game / project
    def choose_game(self):
        start = self.st.game.root if self.st.game else ''
        p = pick_folder('아일렛 온라인이 설치된 폴더 (IsletClient 실행 파일과 data 폴더가 있는 곳)', start)
        if not p:
            return False
        self.st.open_game(p)
        toast('게임 폴더를 열었습니다')
        self.state_changed()
        return True

    def require_game(self) -> bool:
        if self.st.game:
            return True
        QMessageBox.information(self, '게임 폴더', '먼저 아일렛 온라인 설치 폴더를 선택하세요.')
        return guarded(self.choose_game)() or False

    def require_project(self) -> bool:
        if self.st.project:
            return True
        box = QMessageBox(self)
        box.setWindowTitle('모드 프로젝트가 필요합니다')
        box.setText('수정한 파일은 "모드 프로젝트"에 저장됩니다. 게임 파일은 절대 바뀌지 않아요.')
        b_new = box.addButton('새 모드 만들기', QMessageBox.AcceptRole)
        b_open = box.addButton('모드 열기', QMessageBox.ActionRole)
        box.addButton('취소', QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_new:
            return bool(guarded(self.new_project)())
        if box.clickedButton() is b_open:
            return bool(guarded(self.open_project)())
        return False

    def new_project(self):
        dlg = NewProjectDialog(self.st, self)
        while dlg.exec() == QDialog.Accepted:
            try:
                self.st.new_project(dlg.dir.text().strip(), dlg.info())
            except StudioError as ex:
                error(str(ex))
                continue
            toast('새 모드를 만들었습니다')
            self.state_changed()
            return True
        return False

    def open_project(self):
        recent = self.st.settings.get('recent_projects', [])
        start = recent[0] if recent else self.st.default_projects_dir()
        p = pick_folder('모드 프로젝트 폴더 (mod.json이 있는 곳)', start)
        if not p:
            return False
        self.st.open_project(p)
        toast('모드를 열었습니다')
        self.state_changed()
        return True

    def open_capture(self):
        """The small always-on-top world capture window (studio/ui/worldcapture.py)."""
        if not self.require_game():
            return
        if self.capture_win is None:
            from .worldcapture import CaptureWindow
            self.capture_win = CaptureWindow(lambda: self.st.game.root if self.st.game else None)
            self.capture_win.captured.connect(self._world_captured)
        self.capture_win.show()
        self.capture_win.raise_()
        self.capture_win.activateWindow()

    def _world_captured(self, r):
        page = self.pages.get('world')
        if page is not None:
            page.load_saved()

    def closeEvent(self, e):
        cur = self.pages.get(self.current) if self.current else None
        if cur is not None and hasattr(cur, 'can_leave') and not cur.can_leave():
            e.ignore()
            return
        for w in self.pages.values():
            if hasattr(w, 'shutdown'):
                w.shutdown()
        if self.capture_win is not None:
            self.capture_win.close()
        self.agent_link.stop()
        self.st.save_settings()
        super().closeEvent(e)
