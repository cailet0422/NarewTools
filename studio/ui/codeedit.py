"""Plain-text editor with line numbers, XML/HLSL highlighting and a find bar."""
from __future__ import annotations

import re

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from .common import button, hbox


def _fmt(color, bold=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


class XmlHighlighter(QSyntaxHighlighter):
    RULES = [
        (r'<\?[^>]*\?>', '#89ddff'),
        (r'</?[A-Za-z_][\w.:-]*', '#7fb4e8'),
        (r'/?>', '#7fb4e8'),
        (r'\b[A-Za-z_][\w.:-]*(?=\s*=)', '#e0b46a'),
        (r'"[^"]*"|\'[^\']*\'', '#9ccc8a'),
        (r'&[#\w]+;', '#f78c6c'),
    ]

    def __init__(self, doc):
        super().__init__(doc)
        self.rules = [(QRegularExpression(p), _fmt(c)) for p, c in self.RULES]
        self.comment = _fmt('#6b7283')

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)
        self.setCurrentBlockState(0)
        start = 0 if self.previousBlockState() == 1 else text.find('<!--')
        while start >= 0:
            end = text.find('-->', start)
            if end < 0:
                self.setFormat(start, len(text) - start, self.comment)
                self.setCurrentBlockState(1)
                break
            self.setFormat(start, end + 3 - start, self.comment)
            start = text.find('<!--', end + 3)


class CHighlighter(QSyntaxHighlighter):
    KW = ('float', 'float2', 'float3', 'float4', 'float4x4', 'float3x3', 'int', 'bool', 'void', 'return', 'if',
          'else', 'for', 'struct', 'sampler', 'sampler2D', 'texture', 'technique', 'pass', 'uniform', 'const',
          'in', 'out', 'inout', 'static', 'half', 'half3', 'half4', 'true', 'false', 'compile')

    def __init__(self, doc):
        super().__init__(doc)
        self.rules = [(QRegularExpression(r'\b(' + '|'.join(self.KW) + r')\b'), _fmt('#c792ea')),
                      (QRegularExpression(r'\b\d+(\.\d+)?f?\b'), _fmt('#f78c6c')),
                      (QRegularExpression(r'"[^"]*"'), _fmt('#9ccc8a')),
                      (QRegularExpression(r'//[^\n]*'), _fmt('#6b7283')),
                      (QRegularExpression(r'#\w+'), _fmt('#89ddff'))]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class _Gutter(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e):
        self.ed.paint_gutter(e)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFont('Consolas')
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        self.setFont(f)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)
        self.setStyleSheet('QPlainTextEdit { background: #111318; border: none; }')
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(self._update_gutter)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.highlighter = None

    def set_language(self, lang: str | None):
        if self.highlighter:
            self.highlighter.setDocument(None)
        self.highlighter = XmlHighlighter(self.document()) if lang == 'xml' else \
            CHighlighter(self.document()) if lang == 'c' else None

    def gutter_width(self) -> int:
        return 14 + self.fontMetrics().horizontalAdvance('9') * max(3, len(str(self.blockCount())))

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, e):
        p = QPainter(self.gutter)
        p.fillRect(e.rect(), QColor('#15171c'))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        p.setPen(QColor('#555c6b'))
        while block.isValid() and top <= e.rect().bottom():
            if block.isVisible() and bottom >= e.rect().top():
                p.drawText(0, top, self.gutter.width() - 6, self.fontMetrics().height(), Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        p.end()


class CodePanel(QWidget):
    """CodeEditor + find bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = CodeEditor()
        self.find = QLineEdit()
        self.find.setPlaceholderText('찾기 (Enter: 다음)')
        self.find.returnPressed.connect(self.find_next)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.editor)
        lay.addWidget(hbox(self.find, button('다음', self.find_next), button('이전', lambda: self.find_next(True)),
                           margins=(6, 4, 6, 4)))

    def find_next(self, back=False):
        t = self.find.text()
        if not t:
            return
        flags = QTextDocument.FindBackward if back else QTextDocument.FindFlag(0)
        if not self.editor.find(t, flags):
            c = self.editor.textCursor()
            c.movePosition(QTextCursor.End if back else QTextCursor.Start)
            self.editor.setTextCursor(c)
            self.editor.find(t, flags)

    def goto_line(self, n: int):
        block = self.editor.document().findBlockByLineNumber(max(0, n - 1))
        c = QTextCursor(block)
        self.editor.setTextCursor(c)
        self.editor.centerCursor()


def language_for(name: str) -> str | None:
    ext = name.rsplit('.', 1)[-1].lower()
    if ext in ('xml', 'nwmt', 'nwat', 'nwbt', 'scene', 'effect'):
        return 'xml'
    if ext in ('fx', 'fxh', 'hlsl', 'ps', 'vs', 'shader'):
        return 'c'
    return None


def first_error_line(msg: str) -> int | None:
    m = re.search(r'(\d+)줄', msg)
    return int(m.group(1)) if m else None
