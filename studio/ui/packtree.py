"""Lazy tree of every pack (plus loose game files) with cross-pack search."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QComboBox, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from narew.modfmt import entry_key

from ..core import Studio, kind_of
from ..game import LOOSE
from .common import DARK, debounce, fmt_size, hbox

ICON = {'image': '🖼', 'audio': '🔊', 'font': '🔤', 'text': '📄', 'model': '🧍', 'brick': '🧱', 'binary': '▫'}
KINDS = [('', '전체'), ('image', '이미지'), ('audio', '사운드'), ('text', '텍스트'), ('model', '모델'),
         ('font', '폰트'), ('brick', '블록맵')]
ROLE = Qt.UserRole


def _tree(names_sizes):
    root = {'dirs': {}, 'files': []}
    for name, size in names_sizes:
        parts = name.split('\\')
        node = root
        for p in parts[:-1]:
            node = node['dirs'].setdefault(p.lower(), {'name': p, 'dirs': {}, 'files': []})
        node['files'].append((name, size))
    return root


class PackTree(QWidget):
    file_selected = Signal(str)
    dir_selected = Signal(str, str, list)      # pack, folder path, file names below it

    def __init__(self, st: Studio, parent=None):
        super().__init__(parent)
        self.st = st
        self.search = QLineEdit()
        self.search.setPlaceholderText('모든 팩에서 파일 이름 검색…')
        self.search.setClearButtonEnabled(True)
        self.kind = QComboBox()
        for v, t in KINDS:
            self.kind.addItem(t, v)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemClicked.connect(self._click)
        self.tree.currentItemChanged.connect(lambda cur, prev: cur and self._click(cur))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(hbox(self.search, self.kind, margins=(6, 6, 6, 6)))
        lay.addWidget(self.tree)
        self._search_timer = debounce(250, self.reload)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.kind.currentIndexChanged.connect(lambda: self.reload())
        self.trees: dict[str, dict] = {}

    # ------------------------------------------------------------------ building
    def reload(self):
        self.tree.clear()
        self.trees.clear()
        g = self.st.game
        if not g:
            return
        q = self.search.text().strip()
        kind = self.kind.currentData()
        if q or kind:
            self._search(q, kind)
            return
        for stem, r in g.readers.items():
            it = QTreeWidgetItem([f'📦 {stem}.pack', f'{r.trailer.count:,}개'])
            it.setData(0, ROLE, ('pack', stem))
            it.addChild(QTreeWidgetItem(['…']))
            self.tree.addTopLevelItem(it)
        loose = g.loose_names()
        if loose:
            it = QTreeWidgetItem(['📁 Language (팩 밖 파일)', f'{len(loose)}개'])
            it.setData(0, ROLE, ('pack', LOOSE))
            it.addChild(QTreeWidgetItem(['…']))
            self.tree.addTopLevelItem(it)
        self.tree.setColumnWidth(0, max(200, self.tree.width() - 90))

    def _pack_names(self, stem):
        g = self.st.game
        if stem == LOOSE:
            items = [(loc.entry.name, loc.entry.raw_size) for loc in g.index.values() if loc.pack == LOOSE]
        else:
            items = [(e.name, e.raw_size) for e in g.readers[stem]]
        known = {entry_key(n) for n, _ in items}
        for k, (name, path) in self.st.project_files().items():
            if k not in known and self.st.locate_pack(name) == stem:
                items.append((name, os.path.getsize(path)))
        return items

    def _expand(self, item: QTreeWidgetItem):
        data = item.data(0, ROLE)
        if not data or item.childCount() != 1 or item.child(0).data(0, ROLE) is not None:
            return
        item.takeChildren()
        if data[0] == 'pack':
            node = self.trees.get(data[1])
            if node is None:
                node = self.trees[data[1]] = _tree(self._pack_names(data[1]))
            self._fill(item, node, data[1], '')
        elif data[0] == 'dir':
            self._fill(item, data[2], data[1], data[3])

    def _fill(self, parent: QTreeWidgetItem, node: dict, stem: str, path: str):
        for key in sorted(node['dirs']):
            d = node['dirs'][key]
            sub = (path + '\\' if path else '') + d['name']
            it = QTreeWidgetItem([f'📁 {d["name"]}', f'{len(d["files"]) + len(d["dirs"]):,}'])
            it.setData(0, ROLE, ('dir', stem, d, sub))
            it.addChild(QTreeWidgetItem(['…']))
            parent.addChild(it)
        for name, size in sorted(node['files'], key=lambda x: x[0].lower()):
            parent.addChild(self._file_item(name, size, full=False))

    def _file_item(self, name: str, size: int, full: bool) -> QTreeWidgetItem:
        label = name if full else name.split('\\')[-1]
        it = QTreeWidgetItem([f'{ICON[kind_of(name)]} {label}', fmt_size(size)])
        it.setData(0, ROLE, ('file', name))
        it.setToolTip(0, name)
        it.setForeground(1, QBrush(QColor(DARK['faint'])))
        self._mark(it, name)
        return it

    def _mark(self, it: QTreeWidgetItem, name: str):
        s = self.st.status_of(name)
        f = QFont(it.font(0))
        f.setBold(s != 'original')
        it.setFont(0, f)
        color = {'modified': DARK['warn'], 'new': DARK['new'], 'merged': DARK['accent']}.get(s)
        it.setForeground(0, QBrush(QColor(color)) if color else QBrush())

    def _search(self, q: str, kind: str):
        g = self.st.game
        qk = entry_key(q) if q else ''
        n = 0
        names = {k: (loc.entry.name, loc.entry.raw_size) for k, loc in g.index.items()}
        for k, (name, path) in self.st.project_files().items():
            names.setdefault(k, (name, os.path.getsize(path)))
        for k, (name, size) in sorted(names.items()):
            if qk in k and (not kind or kind_of(name) == kind):
                self.tree.addTopLevelItem(self._file_item(name, size, full=True))
                n += 1
                if n >= 3000:
                    self.tree.addTopLevelItem(QTreeWidgetItem(['… 결과가 많아 일부만 표시합니다']))
                    break
        if n == 0:
            self.tree.addTopLevelItem(QTreeWidgetItem(['찾은 파일 없음']))

    # ------------------------------------------------------------------ events
    def _click(self, item: QTreeWidgetItem):
        data = item.data(0, ROLE)
        if not data:
            return
        if data[0] == 'file':
            self.file_selected.emit(data[1])
        elif data[0] == 'dir':
            files = []

            def walk(n):
                files.extend(x for x, _ in n['files'])
                for d in n['dirs'].values():
                    walk(d)
            walk(data[2])
            self.dir_selected.emit(data[1], data[3], files)
        elif data[0] == 'pack':
            node = self.trees.get(data[1]) or _tree(self._pack_names(data[1]))
            self.dir_selected.emit(data[1], '', [n for n, _ in self._pack_names(data[1])] if node else [])

    def update_marks(self):
        """Refresh status colours of the file items that exist (after edits)."""
        def walk(it):
            for i in range(it.childCount()):
                c = it.child(i)
                d = c.data(0, ROLE)
                if d and d[0] == 'file':
                    self._mark(c, d[1])
                walk(c)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            d = top.data(0, ROLE)
            if d and d[0] == 'file':
                self._mark(top, d[1])
            walk(top)

    def select(self, name: str):
        self.search.setText(name.split('\\')[-1])
