"""내 모드: project metadata, changed files and added records, export / install to the mod manager."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFormLayout, QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QTextEdit,
                               QVBoxLayout, QWidget)

from narew.modfmt import ModError

from .. import svc_resources as R
from ..creator import Creator
from .common import (IMAGE_FILTER, badge, button, checker_pixmap, confirm, error, fmt_size, guarded, hbox, info, label,
                     open_file, open_in_shell, save_file, toast)


class ProjectPage(QWidget):
    def __init__(self, st, mw):
        super().__init__()
        self.st, self.mw = st, mw
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 12, 16, 12)

    def _clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def on_state(self):
        if self.isVisible():
            self.on_show()

    def on_show(self, **kw):
        self._clear()
        st = self.st
        if not st.project:
            self.lay.addStretch(1)
            for t, k in (('열린 모드 프로젝트가 없습니다', {'title': True}),
                         ('모드 프로젝트에는 수정한 파일과 추가한 항목만 따로 저장됩니다. 게임 파일은 바뀌지 않습니다.', {'muted': True})):
                lb = label(t, **k)
                lb.setAlignment(Qt.AlignCenter)
                self.lay.addWidget(lb)
            self.lay.addWidget(hbox('stretch', button('새 모드 만들기', self.mw.new_project, primary=True),
                                    button('모드 열기', self.mw.open_project), 'stretch'))
            self.lay.addStretch(2)
            return
        info_ = st.project.read_info()
        form_w = QWidget()
        form = QFormLayout(form_w)
        self.f_name = QLineEdit(info_.name)
        self.f_ver = QLineEdit(info_.version)
        self.f_author = QLineEdit(info_.author)
        self.f_desc = QTextEdit(info_.description)
        self.f_desc.setFixedHeight(70)
        form.addRow('모드 ID', label(info_.id, selectable=True))
        form.addRow('이름', self.f_name)
        form.addRow('버전', self.f_ver)
        form.addRow('만든 사람', self.f_author)
        form.addRow('설명', self.f_desc)
        folder = label(f'<a href="#">{st.project.root}</a>')
        folder.linkActivated.connect(lambda _: open_in_shell(st.project.root))
        form.addRow('폴더', folder)
        form.addRow('', button('정보 저장', self.save_info))
        pv = QLabel()
        pv.setFixedSize(180, 180)
        pv.setAlignment(Qt.AlignCenter)
        pv_path = os.path.join(st.project.root, 'preview.png')
        if os.path.isfile(pv_path):
            pv.setPixmap(checker_pixmap(QPixmap(pv_path).scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        else:
            pv.setText('미리보기 없음')
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.addWidget(pv)
        sv.addWidget(button('미리보기 그림…', self.set_preview))
        sv.addStretch(1)
        self.lay.addWidget(hbox(form_w, side))
        self.lay.addWidget(hbox(button('모드 매니저에 설치', self.install, primary=True),
                                button('배포용 .zip 만들기…', self.export), 'stretch'))
        self.lay.addWidget(label('게임에 적용하려면 "모드 매니저에 설치"를 누른 뒤 NarewModManager에서 "게임에 적용"을 누르세요 (설치한 모드는 켜진 채로 맨 위에 들어갑니다).', faint=True))
        changes = R.changes(st)
        added = Creator(st).added()
        self.lay.addWidget(label(f'바뀐 파일 {sum(1 for c in changes if not c["merge"])}개 · 표에 추가/수정한 항목 {len(added)}개',
                                 title=True))
        t = QTableWidget(len(changes), 5)
        t.setHorizontalHeaderLabels(['파일', '상태', '위치', '크기', ''])
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, c in enumerate(changes):
            it = QTableWidgetItem(c['name'] + ('  (항목 병합)' if c['merge'] else ''))
            t.setItem(r, 0, it)
            t.setCellWidget(r, 1, badge(c['status']))
            pack = c['pack']
            t.setItem(r, 2, QTableWidgetItem('팩 밖 파일' if pack and pack.startswith('@') else f'{pack}.pack' if pack else '—'))
            t.setItem(r, 3, QTableWidgetItem(fmt_size(c['size'])))
            if c['merge']:
                t.setCellWidget(r, 4, button('보기', lambda _=False, n=c['name']: self.show_merge(n)))
            else:
                t.setCellWidget(r, 4, hbox(button('열기', lambda _=False, n=c['name']: self.mw.open_page('resources', name=n)),
                                           button('빼기', lambda _=False, n=c['name']: self.remove(n), danger=True)))
        t.cellDoubleClicked.connect(lambda r, _: not changes[r]['merge'] and self.mw.open_page('resources', name=changes[r]['name']))
        t.horizontalHeader().setStretchLastSection(True)
        t.setColumnWidth(0, 520)
        self.lay.addWidget(t, 1)

    def save_info(self):
        p = self.st.need_project()
        info_ = p.read_info()
        info_.name, info_.version = self.f_name.text().strip(), self.f_ver.text().strip()
        info_.author, info_.description = self.f_author.text().strip(), self.f_desc.toPlainText().strip()
        try:
            p.write_info(info_)
        except ModError as ex:
            error(str(ex))
            return
        toast('저장했습니다')
        self.mw.refresh_header()

    def set_preview(self):
        p = open_file('미리보기 그림', IMAGE_FILTER)
        if p:
            R.set_preview(self.st, p)
            self.on_show()

    def remove(self, name):
        if confirm('빼기', f'{name}을(를) 모드에서 뺄까요?', '빼기', danger=True):
            self.st.revert(name)
            self.mw.refresh_header()
            self.on_show()

    def show_merge(self, name):
        info(self.st.read_patch(name) or '', f'{name} 에 추가/수정하는 항목')

    def export(self):
        info_ = self.st.need_project().read_info()
        p = save_file('배포용 zip', f'{info_.id}-{info_.version}.zip', 'zip (*.zip)')
        if p:
            n = R.export_zip(self.st, p)
            toast(f'만들었습니다: {p} ({n}개 파일)')

    @guarded
    def install(self):
        dest = R.install_to_manager(self.st)
        if dest is None:
            if not confirm('이미 설치됨', '같은 ID의 모드가 모드 매니저에 이미 있습니다. 새 내용으로 바꿀까요?', '바꾸기'):
                return
            dest = R.install_to_manager(self.st, replace=True)
        info('모드 매니저에 설치했습니다.\nNarewModManager를 열어 (게임을 끈 상태에서) "게임에 적용"을 누르세요.')
