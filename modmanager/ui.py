"""NarewModManager native window (Qt)."""
from __future__ import annotations

import os

import time

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QProgressBar, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from narew import codepatch, xmlmerge
from narew.modfmt import ModError, entry_key
from narew.version import VERSION
from studio.core import StudioError
from studio.ui.common import (DARK, button, confirm, error, fmt_size, guarded, hbox, info, label, open_file,
                              open_in_shell, pick_folder, run_task, scaled)

from .core import GameBusy, Manager

MODES = [('pack', '팩 패치 (권장)'), ('loose', '낱개 파일 (실험적, GUI 이미지만 확인됨)')]


def _job(fn, *args):
    """Run a manager call; turn its expected errors into plain messages for the error dialog."""
    try:
        return fn(*args)
    except (GameBusy, ModError) as ex:
        raise StudioError(str(ex))
    except PermissionError as ex:
        raise StudioError(f'파일을 바꿀 수 없습니다 ({ex.filename}). 게임이나 다른 프로그램이 쓰고 있는지 확인하세요.')


class ModWindow(QMainWindow):
    def __init__(self, mgr: Manager):
        super().__init__()
        self.setObjectName('main')
        self.setWindowTitle('NarewModManager — 아일렛 온라인 모드 매니저')
        self.resize(1180, 780)
        self.setAcceptDrops(True)
        self.mgr = mgr
        self.working = False
        self._busy = {}
        self._filling = False

        # ---- header: game folder + mode
        head = QFrame()
        head.setObjectName('head')
        hl = QGridLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        logo = QLabel('<b>Narew</b><b style="color:%s">ModManager</b>' % DARK['accent'])
        logo.setStyleSheet('font-size: 13pt; background: transparent;')
        self.game_lbl = label('', muted=True, selectable=True)
        self.mode = QComboBox()
        for k, t in MODES:
            self.mode.addItem(t, k)
        self.mode.currentIndexChanged.connect(self.set_mode)
        hl.addWidget(logo, 0, 0)
        hl.addWidget(label('게임 폴더', faint=True), 0, 1)
        hl.addWidget(self.game_lbl, 0, 2)
        hl.addWidget(button('바꾸기…', self.choose_game), 0, 3)
        hl.addWidget(button('열기', lambda: open_in_shell(self.mgr.game_root or '')), 0, 4)
        hl.addWidget(label('적용 방식', faint=True), 0, 5)
        hl.addWidget(self.mode, 0, 6)
        from studio.ui.updates import Updater    # GitHub releases: check, download, put in on close
        self.updater = Updater(self, 'NarewModManager.exe')
        hl.addWidget(button(f'v{VERSION}', self.updater.check_now, tip='업데이트 확인'), 0, 7)
        hl.setColumnStretch(2, 1)

        # ---- status banner
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setContentsMargins(14, 10, 14, 10)

        # ---- left: mod list
        self.list = QListWidget()
        self.list.setIconSize(QSize(48, 48))
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSpacing(2)
        # deferred: the list must not be rebuilt inside its own drag/check signal
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self.save_order))
        self.list.itemChanged.connect(lambda *_: self._filling or QTimer.singleShot(0, self.save_order))
        self.list.currentItemChanged.connect(lambda it, _: self.show_mod(it.data(Qt.UserRole) if it else None))
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 6, 10)
        ll.addWidget(label('설치된 모드', title=True))
        ll.addWidget(label('체크한 모드만 적용됩니다. 끌어서 순서를 바꾸세요 — 위에 있을수록 우선합니다.', faint=True, wrap=True))
        ll.addWidget(self.list, 1)
        ll.addWidget(hbox(button('＋ .zip 설치…', self.install_zip, primary=True), button('폴더에서 설치…', self.install_dir),
                          'stretch', button('▲', lambda: self.move(-1), tip='우선순위 올리기'),
                          button('▼', lambda: self.move(1), tip='우선순위 내리기')))
        self.reload_btn = button('⟳ 새로고침', self.reload_mods,
                                 tip='모드 폴더·설정·게임 폴더 상태를 다시 읽습니다 (F5)')
        ll.addWidget(hbox(button('모드 폴더 열기', lambda: open_in_shell(self.mgr.mods_dir)), self.reload_btn,
                          'stretch', button('제거', self.remove, danger=True)))
        QShortcut(QKeySequence(Qt.Key_F5), self, self.reload_mods)

        # ---- right: details
        self.preview = QLabel()
        self.preview.setFixedSize(200, 150)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(f'background: {DARK["bg2"]}; border-radius: 6px;')
        self.title = label('', title=True, wrap=True)
        self.meta = label('', muted=True, wrap=True)
        self.desc = label('', wrap=True, selectable=True)
        top = QWidget()
        tl = QGridLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.preview, 0, 0, 3, 1)
        tl.addWidget(self.title, 0, 1)
        tl.addWidget(self.meta, 1, 1)
        tl.addWidget(self.desc, 2, 1, alignment=Qt.AlignTop)
        tl.setColumnStretch(1, 1)
        self.files = self._table(['파일', '크기', '상태'])
        self.merges = self._table(['게임 표', '추가·수정하는 항목', '함께 고치는 모드'])
        self.patches = self._table(['코드 패치', '바꾸는 파일', '상태'])
        self.tabs = QTabWidget()
        self.tabs.addTab(self.files, '파일')
        self.tabs.addTab(self.merges, '표 병합')
        self.tabs.addTab(self.patches, '코드 패치')
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 10, 10, 10)
        rl.addWidget(top)
        rl.addWidget(self.tabs, 1)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([430, 750])

        # ---- bottom actions
        self.apply_btn = button('✔  게임에 적용', self.apply, primary=True)
        self.apply_btn.setMinimumHeight(36)
        self.restore_btn = button('원본 복구 (모드 모두 빼기)', self.restore, danger=True)
        self.restore_btn.setMinimumHeight(36)
        bottom = hbox(label('적용 전에 게임을 종료하세요. 원본은 백업해 두었다가 "원본 복구"로 그대로 되돌립니다.', faint=True),
                      'stretch', self.restore_btn, self.apply_btn, margins=(12, 8, 12, 10))

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(head)
        v.addWidget(self.banner)
        v.addWidget(split, 1)
        v.addWidget(bottom)
        self.setCentralWidget(central)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        self.busy_label = label('', muted=True)
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.refresh()

    @staticmethod
    def _table(headers) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(True)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(headers)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        return t

    # ------------------------------------------------------------------ run_task hooks (see studio.ui.common)
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
        if msg:
            self.statusBar().showMessage(msg, 3000)

    # ------------------------------------------------------------------ state
    def refresh(self, select: str | None = None):
        mgr = self.mgr
        if not self.working:
            mgr.reload()            # NarewStudio or another program may have changed the files
        self._refreshed = time.monotonic()
        updated = mgr.check()['updated'] if mgr.game_root else []
        cur = select or (self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None)
        self.game_lbl.setText(mgr.game_root or mgr.settings.get('game') or '(선택 안 됨)')
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mgr.settings.get('mode', 'pack'))))
        self.mode.blockSignals(False)
        self.win, self.conflicts = mgr.winners()
        self.mrg = mgr.merges()
        self._filling = True
        self.list.clear()
        for m in mgr.installed():
            it = QListWidgetItem()
            it.setData(Qt.UserRole, m['id'])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Checked if m['enabled'] else Qt.Unchecked)
            if 'error' in m:
                it.setText(f"{m['id']}\n⚠ {m['error']}")
                it.setForeground(Qt.red)
            else:
                inf = m['info']
                nf = sum(1 for _ in m['folder'].iter_files())
                nm = sum(1 for _ in m['folder'].iter_merges())
                npt = sum(1 for _ in m['folder'].iter_patches())
                lost = sum(1 for n, _ in m['folder'].iter_files()
                           if m['enabled'] and self.win.get(entry_key(n), (0, 0, m['id']))[2] != m['id'])
                bits = [f'v{inf.version}'] + ([inf.author] if inf.author else []) + [f'파일 {nf}']
                if nm:
                    bits.append(f'표 병합 {nm}')
                if npt:
                    bits.append(f'코드 패치 {npt}')
                if lost:
                    bits.append(f'⚠ 가려진 파일 {lost}')
                it.setText(f"{inf.name}\n{' · '.join(bits)}")
                pv = os.path.join(m['folder'].root, 'preview.png')
                if os.path.isfile(pv):
                    it.setIcon(QIcon(scaled(QPixmap(pv), 48, 48)))
            self.list.addItem(it)
            if m['id'] == cur:
                self.list.setCurrentItem(it)
        self._filling = False
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        if not self.list.count():
            self.show_mod(None)
        self.update_banner(updated)

    def reload_mods(self):
        """Button / F5: read the mods folder, settings and the game folder again (mods copied in by hand,
        NarewStudio's "install", a Steam update)."""
        if self.working:
            self.statusBar().showMessage('적용·복구가 끝난 뒤에 새로고침하세요.', 3000)
            return
        self.refresh()
        self.statusBar().showMessage(f'새로고침했습니다 — 설치된 모드 {self.list.count()}개', 3000)

    def update_banner(self, updated=()):
        mgr = self.mgr
        st = mgr.status()
        enabled = [m for m in mgr.installed() if m['enabled'] and 'info' in m]
        if not mgr.game_root:
            text, color = '게임 폴더를 고르세요 (IsletClient 실행 파일과 data 폴더가 있는 곳).', DARK['warn']
        elif updated or st['stale']:
            text, color = ('게임이 업데이트(또는 파일 검사)되어 일부 모드가 풀렸습니다. [게임에 적용]을 다시 누르세요.'
                           + (f"  ({', '.join(updated)})" if updated else '')), DARK['warn']
        elif st['applied'] and st['in_sync']:
            text, color = f"모드 {len(st['applied_mods'])}개가 게임에 적용되어 있습니다.", DARK['accent']
        elif not st['in_sync']:
            text, color = ('바뀐 설정이 아직 게임에 들어가지 않았습니다. [게임에 적용]을 누르세요.' if enabled or st['applied']
                           else '켠 모드가 없습니다.'), DARK['warn'] if enabled or st['applied'] else DARK['dim']
        else:
            text, color = '게임은 원본 상태입니다.', DARK['dim']
        if self.conflicts:
            text += f'   ·   같은 파일을 바꾸는 모드가 있습니다 ({len(self.conflicts)}개 파일, 위쪽 모드 우선).'
        self.banner.setText(text)
        self.banner.setStyleSheet(f'background: {DARK["bg2"]}; color: {color}; border-bottom: 1px solid {DARK["line"]};')
        can = bool(mgr.game_root) and not self.working
        self.apply_btn.setEnabled(can)
        self.restore_btn.setEnabled(not self.working and bool(st['applied']))

    def show_mod(self, mod_id: str | None):
        self.files.setRowCount(0)
        self.merges.setRowCount(0)
        self.patches.setRowCount(0)
        m = next((x for x in self.mgr.installed() if x['id'] == mod_id), None) if mod_id else None
        if not m or 'info' not in m:
            self.preview.clear()
            self.title.setText(m['id'] if m else '모드를 고르세요')
            self.meta.setText(m.get('error', '') if m else '')
            self.desc.setText('')
            return
        inf = m['info']
        pv = os.path.join(m['folder'].root, 'preview.png')
        self.preview.setPixmap(scaled(QPixmap(pv), 200, 150) if os.path.isfile(pv) else QPixmap())
        if not os.path.isfile(pv):
            self.preview.setText('미리보기 없음')
        self.title.setText(inf.name)
        self.meta.setText(' · '.join(x for x in (f'v{inf.version}', inf.author, inf.id) if x))
        self.desc.setText(inf.description or '')
        rows = list(m['folder'].iter_files())
        self.files.setRowCount(len(rows))
        for r, (name, full) in enumerate(rows):
            k = entry_key(name)
            w = self.win.get(k)
            others = [x for x in self.conflicts.get(k, []) if x != m['id']]
            if not m['enabled']:
                state = '꺼짐'
            elif k in self.mrg:
                state = '표 병합의 바탕으로 쓰임'
            elif w and w[2] != m['id']:
                state = f'⚠ {w[2]} 모드에 가려짐'
            elif others:
                state = f"우선 적용 ({', '.join(others)} 보다 위)"
            else:
                state = '적용'
            for c, t in enumerate((name, fmt_size(os.path.getsize(full)), state)):
                self.files.setItem(r, c, QTableWidgetItem(t))
        merges = list(m['folder'].iter_merges())
        self.merges.setRowCount(len(merges))
        for r, (name, full) in enumerate(merges):
            try:
                with open(full, encoding='utf-8') as f:
                    n = len(xmlmerge.parse_patch(f.read()))
                count = f'{n}개'
            except (OSError, xmlmerge.MergeError) as ex:
                count = f'⚠ {ex}'
            others = [x for x, _ in self.mrg.get(entry_key(name), ('', []))[1] if x != m['id']]
            for c, t in enumerate((name, count, ', '.join(others) or '-')):
                self.merges.setItem(r, c, QTableWidgetItem(t))
        patches = list(m['folder'].iter_patches())
        applied = {u for us in self.mgr.status()['binaries'].values() for u in us}
        self.patches.setRowCount(len(patches))
        for r, (name, full) in enumerate(patches):
            try:
                pf = codepatch.load(full)
                files = ', '.join(t.file for t in pf.targets)
                text = name + (f' — {pf.description}' if pf.description else '')
                state = ('적용됨' if f"{m['id']}/{name}" in applied else '꺼짐' if not m['enabled']
                         else '아직 적용 안 됨')
            except (OSError, codepatch.CodePatchError) as ex:
                text, files, state = name, '-', f'⚠ {ex}'
            for c, t in enumerate((text, files, state)):
                self.patches.setItem(r, c, QTableWidgetItem(t))
        self.tabs.setTabText(0, f'파일 {len(rows)}')
        self.tabs.setTabText(1, f'표 병합 {len(merges)}')
        self.tabs.setTabText(2, f'코드 패치 {len(patches)}')

    # ------------------------------------------------------------------ actions
    def save_order(self):
        if self._filling:
            return
        entries = [{'id': self.list.item(i).data(Qt.UserRole), 'enabled': self.list.item(i).checkState() == Qt.Checked}
                   for i in range(self.list.count())]
        self.mgr.set_order(entries)
        cur = self.list.currentItem()
        self.refresh(cur.data(Qt.UserRole) if cur else None)

    def move(self, d: int):
        row = self.list.currentRow()
        if row < 0 or not 0 <= row + d < self.list.count():
            return
        self._filling = True
        it = self.list.takeItem(row)
        self.list.insertItem(row + d, it)
        self.list.setCurrentItem(it)
        self._filling = False
        self.save_order()

    def set_mode(self):
        self.mgr.settings['mode'] = self.mode.currentData()
        self.mgr.save_settings()
        self.refresh()

    def choose_game(self):
        p = pick_folder('아일렛 온라인 설치 폴더', self.mgr.settings.get('game') or '')
        if p:
            try:
                self.mgr.set_game(p)
            except ModError as ex:
                error(str(ex))
            self.refresh()

    def install_zip(self):
        p = open_file('모드 .zip', '모드 (*.zip)')
        if p:
            self.install(p)

    def install_dir(self):
        p = pick_folder('mod.json이 있는 모드 폴더')
        if p:
            self.install(p)

    @guarded
    def install(self, path: str, replace: bool = False):
        if os.path.isfile(path) and os.path.basename(path).lower() == 'mod.json':
            path = os.path.dirname(path)
        try:
            if os.path.isdir(path):
                mid = self.mgr.install_folder(path, replace)
            elif path.lower().endswith('.zip') and os.path.isfile(path):
                mid = self.mgr.install_zip(path, replace)
            else:
                error('모드 .zip 파일이나 mod.json이 있는 폴더를 고르세요')
                return
        except FileExistsError as ex:
            if confirm('이미 설치된 모드', f'"{ex}" 모드가 이미 있습니다. 새 것으로 바꿀까요?', '바꾸기'):
                self.install(path, True)
            return
        except ModError as ex:
            error(str(ex))
            return
        self.statusBar().showMessage(f'설치했습니다: {mid}', 5000)
        self.refresh(mid)

    def remove(self):
        it = self.list.currentItem()
        if not it:
            return
        mid = it.data(Qt.UserRole)
        if confirm('모드 제거', f'"{mid}" 모드를 지울까요?\n게임에 적용된 상태라면 다음 [게임에 적용] 때 빠집니다.', '지우기', danger=True):
            self.mgr.remove(mid)
            self.refresh()

    def _run(self, title: str, fn, done):
        if self.working:
            return
        self.refresh()              # act on what is on disk now, not on what the window last showed
        self.working = True
        self.update_banner()

        def finish(res=None, msg=None):
            self.working = False
            self.refresh()
            if msg is not None:
                error(msg)
            else:
                done(res)
        run_task(lambda prog: _job(fn, prog), done=lambda r: finish(r), failed=lambda m: finish(msg=m),
                 busy=title, progress=True)

    def apply(self):
        self._run('모드 적용', lambda prog: self.mgr.apply(prog), self._applied)

    def _applied(self, r: dict):
        lines = []
        if r['packs']:
            lines.append('패치한 팩: ' + ', '.join(f'{k}.pack ({v}개)' for k, v in sorted(r['packs'].items())))
        if r['restored']:
            lines.append('원래대로 돌린 팩: ' + ', '.join(f'{k}.pack' for k in r['restored']))
        if r['merged']:
            lines.append('표 병합: ' + ', '.join(f"{k} (+{v['added']}, 수정 {v['replaced']})"
                                                for k, v in sorted(r['merged'].items())))
        if r['loose']:
            lines.append(f"게임 폴더에 넣은 파일: {r['loose']}개")
        if r['overridden']:
            lines.append('백업 후 바꾼 게임 파일: ' + ', '.join(r['overridden']))
        for fname, used in sorted(r.get('binaries', {}).items()):
            lines.append(f'코드 패치 ({fname}, 백업 후): ' + ', '.join(used))
        if r['conflicts']:
            lines.append(f"같은 파일을 바꾸는 모드: {r['conflicts']}개 파일 (위쪽 모드 우선)")
        if r['skipped']:
            lines.append('건너뜀:\n  ' + '\n  '.join(r['skipped'][:20]))
        info('\n'.join(lines) or '적용할 모드가 없어 게임을 원본으로 두었습니다.', '적용 완료')

    def restore(self):
        if confirm('원본 복구', '게임에 넣은 모드를 모두 빼고 원본 파일로 되돌릴까요?\n(설치된 모드는 지워지지 않습니다)', '복구'):
            self._run('원본 복구', lambda prog: self.mgr.restore_all(),
                      lambda r: info(f"복구했습니다. 팩·DLL {len(r['restored'])}개, 파일 {r['loose']}개", '복구 완료'))

    # ------------------------------------------------------------------ drag & drop install
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.install(url.toLocalFile())

    def changeEvent(self, e):
        # coming back to the window: show changes made elsewhere (e.g. NarewStudio's "install")
        if (e.type() == QEvent.ActivationChange and self.isActiveWindow() and not self.working
                and time.monotonic() - getattr(self, '_refreshed', 0) > 1.0):
            QTimer.singleShot(0, self.refresh)
        super().changeEvent(e)

    def closeEvent(self, e):
        if self.working and not confirm('작업 중', '적용·복구가 끝나지 않았습니다. 그래도 닫을까요?', '닫기', danger=True):
            e.ignore()
            return
        e.accept()
