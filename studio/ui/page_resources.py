"""리소스 편집: browse every pack, edit any file into the mod project."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from .editors import ResourceEditor
from .packtree import PackTree


class ResourcesPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.tree = PackTree(st)
        self.editor = ResourceEditor(st)
        self.tree.file_selected.connect(self._select)
        self.editor.changed.connect(lambda _: self.tree.update_marks())
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree)
        split.addWidget(self.editor)
        split.setSizes([360, 1000])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.loaded_for = None

    def _select(self, name):
        if name == self.editor.name:
            return
        if self.editor.can_leave():
            self.editor.open(name)

    def on_show(self, name: str | None = None):
        if self.st.game and self.loaded_for != self.st.game.root:
            self.loaded_for = self.st.game.root
            self.tree.reload()
        if name:
            self.editor.open(name)

    def on_state(self):
        self.loaded_for = None
        self.tree.reload()
        if self.editor.name:
            self.editor.open(self.editor.name)

    def can_leave(self) -> bool:
        return self.editor.can_leave()

    def shutdown(self):
        self.editor.stop_audio()
