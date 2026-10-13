"""QOpenGLWidget hosting a moderngl context with an orbit camera."""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..gl.mathx import OrbitCamera


def set_default_gl_format():
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


class GLView(QOpenGLWidget):
    """Subclasses implement init_gl(ctx) and draw(ctx, w, h)."""

    gl_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctx = None
        self.cam = OrbitCamera()
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._drag = None
        self._last = QPoint()
        self.rotate_button = Qt.LeftButton
        self.pan_button = Qt.MiddleButton
        self.animating = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self._t = time.perf_counter()
        self.error = None

    # ---- GL lifecycle
    def initializeGL(self):
        try:
            import moderngl
            self.ctx = moderngl.create_context()
            self.init_gl(self.ctx)
        except Exception as ex:
            self.error = str(ex)
            self.ctx = None
            self.gl_failed.emit(f'OpenGL 3.3을 쓸 수 없습니다: {ex}')

    def paintGL(self):
        if not self.ctx:
            return
        dpr = self.devicePixelRatioF()
        w, h = int(self.width() * dpr), int(self.height() * dpr)
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()
        self.ctx.viewport = (0, 0, w, h)
        self.draw(self.ctx, w, h)

    def init_gl(self, ctx):
        pass

    def draw(self, ctx, w, h):
        pass

    def tick(self, dt: float) -> bool:
        """Return True to request a repaint."""
        return self.animating

    def _tick(self):
        now = time.perf_counter()
        dt, self._t = now - self._t, now
        if self.isVisible() and self.tick(min(dt, 0.1)):
            self.update()

    # ---- camera input
    def mousePressEvent(self, e):
        self._last = e.position().toPoint()
        if e.button() in (self.rotate_button, self.pan_button):
            self._drag = e.button()
        self.on_press(e)

    def mouseReleaseEvent(self, e):
        if e.button() == self._drag:
            self._drag = None
        self.on_release(e)

    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        d = p - self._last
        self._last = p
        if self._drag == self.rotate_button:
            self.cam.rotate(d.x(), d.y())
            self.camera_changed()
            self.update()
        elif self._drag == self.pan_button:
            self.cam.pan(d.x(), d.y(), self.height())
            self.camera_changed()
            self.update()
        self.on_move(e)

    def wheelEvent(self, e):
        self.cam.zoom(e.angleDelta().y() / 120)
        self.camera_changed()
        self.update()

    def on_press(self, e):
        pass

    def on_release(self, e):
        pass

    def on_move(self, e):
        pass

    def camera_changed(self):
        pass
