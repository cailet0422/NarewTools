"""Update check / download / install in NarewStudio and NarewModManager (studio.selfupdate does the work).

On start (at most every few hours, can be turned off) the latest GitHub release is checked in the background;
a newer one is offered with its release notes: update / later / skip this version.  "Update" downloads and
verifies it, then the update is put in when the program closes (now, or whenever the user closes it): the new
version's NarewAgent.exe replaces the files and starts the program again.  The version button in the header
checks right away.
"""
from __future__ import annotations

import os
import traceback

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox

from narew.version import VERSION

from .. import selfupdate
from .common import confirm, error, info, run_task, toast


class Updater(QObject):
    def __init__(self, win, program: str):
        """program: the exe to start again after updating, e.g. 'NarewStudio.exe'."""
        super().__init__(win)
        self.win, self.program = win, program
        self.pending = None               # a downloaded update, put in when the program quits
        self.working = False
        QApplication.instance().aboutToQuit.connect(self._install_pending)

    def start(self):
        """After the window is shown: report the last update, tidy old downloads, check if it is time."""
        QTimer.singleShot(1500, self._startup)

    def _startup(self):
        r = selfupdate.take_report()
        if r and r.get('ok'):
            toast(f"NarewTools {r['version']}(으)로 업데이트했습니다", 10000)
        elif r:
            error(f"NarewTools {r.get('version')} 업데이트를 넣지 못했습니다. 프로그램은 그대로입니다.\n\n"
                  f"{r.get('error')}\n\n기록: {r.get('log')}", '업데이트')
        try:
            selfupdate.cleanup()
        except OSError:
            pass
        if selfupdate.frozen_dir() and selfupdate.due():
            run_task(selfupdate.check, done=lambda i: self._found(i, manual=False), failed=lambda m: None)

    def check_now(self):
        if self.working:
            toast('업데이트를 받는 중입니다')
            return
        if self.pending:
            self._ready(self.pending)
            return
        run_task(selfupdate.check, busy='업데이트 확인', done=lambda i: self._found(i, manual=True),
                 failed=lambda m: error(m, '업데이트 확인'))

    def _found(self, rel, manual: bool):
        if rel is None:
            if manual:
                info(f'지금 버전 {VERSION}이(가) 최신입니다.', '업데이트')
            return
        if not manual and selfupdate.settings().get('skip') == rel['version']:
            return
        self._ask(rel)

    def _ask(self, rel):
        frozen = selfupdate.frozen_dir()
        box = QMessageBox(self.win)
        box.setWindowTitle('NarewTools 업데이트')
        box.setText(f"새 버전 <b>{rel['version']}</b>이(가) 나왔습니다. (지금 {VERSION})")
        notes = rel.get('notes') or ''
        how = ('업데이트를 누르면 받아서 확인한 뒤, 프로그램을 닫을 때 새 버전으로 바꾸고 다시 엽니다. '
               '모드 프로젝트·설정·내 월드는 그대로입니다.' if frozen and rel.get('zip_url') else
               '이 버전은 릴리스 페이지에서 받으세요.' if frozen else
               '소스에서 실행 중입니다: git pull로 받으세요.')
        box.setInformativeText(how)
        if notes:
            box.setDetailedText(notes)
        b_up = box.addButton('업데이트' if frozen and rel.get('zip_url') else '릴리스 페이지 열기',
                             QMessageBox.AcceptRole)
        box.addButton('나중에', QMessageBox.RejectRole)
        b_skip = box.addButton('이 버전 건너뛰기', QMessageBox.ActionRole)
        auto = QCheckBox('프로그램을 켤 때 업데이트 확인')
        auto.setChecked(selfupdate.settings().get('auto_check', True))
        box.setCheckBox(auto)
        box.exec()
        selfupdate.set_option(auto_check=auto.isChecked())
        clicked = box.clickedButton()
        if clicked is b_skip:
            selfupdate.set_option(skip=rel['version'])
            toast(f"{rel['version']}은(는) 자동으로 묻지 않습니다 (버전 버튼으로 다시 확인)")
        elif clicked is b_up:
            if frozen and rel.get('zip_url'):
                self._download(rel, frozen)
            else:
                QDesktopServices.openUrl(QUrl(rel.get('page') or selfupdate.PAGE))

    def _download(self, rel, folder: str):
        if not selfupdate.writable(folder):
            error(f'프로그램 폴더에 쓸 수 없어 자동으로 바꿀 수 없습니다:\n{folder}\n\n'
                  '폴더를 문서 등 쓸 수 있는 곳으로 옮기거나, 릴리스 페이지에서 새 zip을 받으세요.', '업데이트')
            return
        self.working = True

        def done(job):
            self.working = False
            self._ready(job)

        def failed(msg):
            self.working = False
            error(msg, '업데이트')
        run_task(lambda progress: selfupdate.download(rel, progress), progress=True, busy='업데이트 받는 중',
                 done=done, failed=failed)

    def _ready(self, job):
        self.pending = job
        folder = selfupdate.frozen_dir()
        others = selfupdate.running_from(folder, exclude={os.getpid()}) if folder else []
        msg = (f"NarewTools {job['version']}을(를) 받았습니다.\n프로그램을 닫으면 새 버전으로 바꾸고 다시 엽니다 "
               '(바꾸기 전 파일은 백업합니다).')
        if others:
            msg += ('\n\n함께 닫아야 하는 프로그램: ' + ', '.join(sorted({os.path.basename(p) for _, p in others}))
                    + '\n(2분 안에 닫지 않으면 이번 업데이트는 넣지 않습니다)')
        if confirm('업데이트 준비 완료', msg, '지금 닫고 업데이트'):
            self.win.close()
        else:
            toast('프로그램을 닫을 때 업데이트합니다')

    def _install_pending(self):
        if not self.pending:
            return
        folder = selfupdate.frozen_dir()
        if not folder:
            return
        try:
            selfupdate.launch(self.pending, folder, self.program)
        except Exception:
            traceback.print_exc()             # no window any more: the log file has it
