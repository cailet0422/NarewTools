"""팩 탐색기: pack-level view (details, extract, verify, build)."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from narew.pack import PackReader, PackWriter, iter_files, name_key, write_atomic

from ..game import LOOSE, Located
from .common import (button, error, fmt_size, hbox, info, label, open_file, open_in_shell, pick_folder, run_task,
                     save_file, toast)
from .packtree import PackTree


class ExplorerPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.tree = PackTree(st)
        self.tree.file_selected.connect(self.show_entry)
        self.tree.dir_selected.connect(self.show_dir)
        self.right = QWidget()
        self.rlay = QVBoxLayout(self.right)
        self.rlay.setContentsMargins(14, 12, 14, 12)
        self.verify_box = QCheckBox('추출할 때 MD5 검증')
        self.verify_box.setChecked(True)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree)
        split.addWidget(self.right)
        split.setSizes([380, 1000])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.loaded_for = None

    def _clear(self):
        while self.rlay.count():
            it = self.rlay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def on_show(self, **kw):
        if not self.st.game:
            self._clear()
            self.rlay.addWidget(label('먼저 게임 폴더를 선택하세요', muted=True))
            return
        if self.loaded_for != self.st.game.root:
            self.loaded_for = self.st.game.root
            self.tree.reload()
        self.overview()

    def on_state(self):
        self.loaded_for = None
        self.on_show()

    def overview(self):
        self._clear()
        packs = self.st.game.pack_summary()
        self.rlay.addWidget(label('팩 목록', title=True))
        self.rlay.addWidget(label(self.st.game.root, muted=True, selectable=True))
        self.rlay.addWidget(hbox(button('전체 추출…', lambda: self.extract([p['pack'] for p in packs], []), primary=True),
                                 button('전체 검증', self.verify), button('폴더로 팩 만들기…', self.build),
                                 button('다른 .pack 파일 보기…', self.open_external), self.verify_box, 'stretch'))
        t = QTableWidget(len(packs), 5)
        t.setHorizontalHeaderLabels(['팩', '파일', '크기', '풀었을 때', ''])
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, p in enumerate(packs):
            name = p['pack'] + '.pack' + ('  (백업에서 읽음)' if p['from_backup'] else '')
            for c, v in enumerate([name, f'{p["count"]:,}', fmt_size(p['size']), fmt_size(p['raw'])]):
                it = QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, c, it)
            t.setCellWidget(r, 4, button('추출', lambda _=False, s=p['pack']: self.extract([s], [])))
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        self.rlay.addWidget(t, 1)
        total = sum(p['count'] for p in packs)
        self.rlay.addWidget(label(f'총 {total:,}개 파일. 왼쪽에서 파일이나 폴더를 고르면 자세히 보여 줍니다.', faint=True))

    def show_entry(self, name):
        self._clear()
        st = self.st
        loc = st.game.locate(name)
        self.rlay.addWidget(label(st.canonical_name(name), title=True, selectable=True))
        card = QFrame()
        card.setObjectName('card')
        g = QGridLayout(card)
        rows = [('모드 상태', {'modified': '이 모드에서 수정됨', 'new': '이 모드의 새 파일', 'merged': '이 모드가 항목을 추가함'}
                 .get(st.status_of(name), '수정 안 됨'))]
        if loc:
            e = loc.entry
            rows = [('위치', '팩 밖 파일 (' + e.path + ')' if loc.pack == LOOSE else f'{loc.pack}.pack'),
                    ('원본 크기', f'{fmt_size(e.raw_size)} ({e.raw_size:,} B)'),
                    ('저장 방식', f'zlib, {fmt_size(e.packed_size)}' if e.compressed else '압축 없음'),
                    ('속성 바이트', str(e.attribute)), ('데이터 위치', f'0x{e.offset:x}'),
                    ('MD5', e.md5.hex() if loc.pack != LOOSE else '-')] + rows
        for i, (k, v) in enumerate(rows):
            g.addWidget(label(k, faint=True), i, 0)
            g.addWidget(label(v, selectable=True), i, 1)
        g.setColumnStretch(1, 1)
        self.rlay.addWidget(card)

        def save_orig():
            p = save_file('원본 파일 저장', name.split('\\')[-1])
            if p:
                with open(p, 'wb') as f:
                    f.write(st.read(name, 'game'))
                toast('저장했습니다: ' + p)
        self.rlay.addWidget(hbox(button('리소스 편집에서 열기', lambda: self.mw.open_page('resources', name=name), primary=True),
                                 button('원본 파일 저장…', save_orig), 'stretch'))
        self.rlay.addStretch(1)

    def show_dir(self, pack, path, files):
        self._clear()
        self.rlay.addWidget(label(f'{pack}.pack \\ {path}' if path else f'{pack}.pack', title=True))
        self.rlay.addWidget(label(f'파일 {len(files):,}개', muted=True))
        self.rlay.addWidget(hbox(button('이 폴더 추출…', lambda: self.extract([], files), primary=True), 'stretch'))
        self.rlay.addStretch(1)

    # ------------------------------------------------------------------ jobs
    def extract(self, packs, names):
        dest = pick_folder('추출할 폴더')
        if not dest:
            return
        st, verify = self.st, self.verify_box.isChecked()

        def job(progress):
            g = st.game
            todo = [(s, e) for s in packs if s != LOOSE for e in g.readers[s]]
            for n in names:
                loc = g.locate(n)
                if loc:
                    todo.append((loc.pack, loc.entry))
            manifests = {}
            for i, (stem, e) in enumerate(todo):
                data = g.read_located(Located(stem, e)) if stem == LOOSE else g.readers[stem].read(e, verify=verify)
                parts = [x for x in e.name.split('\\') if x and x not in ('.', '..') and ':' not in x]
                fp = os.path.join(dest, stem.strip('@'), *parts)
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, 'wb') as f:
                    f.write(data)
                manifests.setdefault(stem, []).append({'name': e.name, 'attribute': e.attribute, 'md5': e.md5.hex()})
                if i % 25 == 0:
                    progress(i + 1, len(todo), e.name)
            for stem in packs:
                with open(os.path.join(dest, stem + '.narew.json'), 'w', encoding='utf-8') as f:
                    json.dump({'pack': stem + '.pack', 'entries': manifests.get(stem, [])}, f, indent=1)
            return len(todo)

        def done(n):
            toast(f'{n:,}개 파일을 추출했습니다')
            open_in_shell(dest)
        run_task(job, done=done, busy='추출', progress=True)

    def verify(self):
        st = self.st

        def job(progress):
            g = st.game
            entries = [(s, e) for s in g.readers for e in g.readers[s]]
            bad = []
            for i, (s, e) in enumerate(entries):
                try:
                    g.readers[s].read(e, verify=True)
                except Exception as ex:
                    bad.append(f'{s}: {e.name}: {ex}')
                if i % 100 == 0:
                    progress(i + 1, len(entries), e.name)
            return len(entries), bad

        def done(r):
            n, bad = r
            if bad:
                error(f'문제 {len(bad)}개:\n' + '\n'.join(bad[:10]))
            else:
                info(f'검증 완료: {n:,}개 모두 정상')
        run_task(job, done=done, busy='검증', progress=True)

    def build(self):
        folder = pick_folder('팩으로 만들 폴더 (추출한 폴더, 예: …\\xml)')
        if not folder:
            return
        out = save_file('저장할 팩 파일', os.path.basename(folder.rstrip('\\/')) + '.pack', '팩 (*.pack)')
        if not out:
            return
        if self.st.game and os.path.normcase(os.path.abspath(out)).startswith(os.path.normcase(self.st.game.data_dir)):
            error('게임 data 폴더에는 직접 쓰지 않습니다. 모드 매니저로 적용하세요.')
            return
        manifest = folder.rstrip('\\/') + '.narew.json'

        def job(progress):
            files = {name_key(n): (n, p) for n, p in iter_files(folder)}
            w = PackWriter()
            used = set()
            if os.path.isfile(manifest):
                with open(manifest, encoding='utf-8') as f:
                    for m in json.load(f).get('entries', []):
                        k = name_key(m['name'])
                        if k in files:
                            with open(files[k][1], 'rb') as fh:
                                w.add(m['name'], fh.read(), m.get('attribute', 0))
                            used.add(k)
            for k, (n, p) in sorted(files.items()):
                if k not in used:
                    with open(p, 'rb') as fh:
                        w.add(n, fh.read())
            t = write_atomic(out, w, lambda i, total, n: i % 50 == 0 and progress(i, total, n))
            return t
        run_task(job, done=lambda t: info(f'만들었습니다: {out}\n{t.count:,}개, {fmt_size(t.file_size)}'),
                 busy='팩 만들기', progress=True)

    def open_external(self):
        p = open_file('볼 .pack 파일', '팩 (*.pack)')
        if not p:
            return
        with PackReader(p) as r:
            items = [(e.name, e.raw_size, e.compressed) for e in r]
            size = r.trailer.file_size
        self._clear()
        self.rlay.addWidget(label(p, title=True, selectable=True))
        self.rlay.addWidget(label(f'{len(items):,}개 파일, {fmt_size(size)}', muted=True))
        t = QTableWidget(min(len(items), 20000), 3)
        t.setHorizontalHeaderLabels(['이름', '크기', '압축'])
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, (n, s, c) in enumerate(items[:20000]):
            t.setItem(r, 0, QTableWidgetItem(n))
            t.setItem(r, 1, QTableWidgetItem(fmt_size(s)))
            t.setItem(r, 2, QTableWidgetItem('zlib' if c else '-'))
        t.horizontalHeader().setStretchLastSection(True)
        t.setColumnWidth(0, 600)
        self.rlay.addWidget(t, 1)
