"""OpenGL model view: loads a .glb produced by ModelService and plays its animation."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage

from ..gl.gltfview import GltfModel, ModelRenderer, frame_for
from .glview import GLView


class ModelView(GLView):
    frame_changed = Signal(float, float)        # time, duration

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ren: ModelRenderer | None = None
        self.model: GltfModel | None = None
        self.pending = None
        self.anim = None
        self.time = 0.0
        self.speed = 1.0
        self.playing = True
        self.grid_y, self.grid_scale = 0.0, 1.0
        self.rotate_button = Qt.LeftButton
        self.pan_button = Qt.RightButton
        self.cam.yaw, self.cam.pitch = math.radians(30), math.radians(15)

    def init_gl(self, ctx):
        self.ren = ModelRenderer(ctx)
        if self.pending is not None:
            self._apply(self.pending)

    def set_model(self, model: GltfModel | None, keep_camera: bool = False):
        self.pending = (model, keep_camera)
        if self.ren is not None:
            self.makeCurrent()
            self._apply(self.pending)
            self.doneCurrent()
        self.update()

    def _apply(self, pending):
        model, keep = pending
        self.pending = None
        self.model = model
        self.ren.set_model(model)
        self.anim = model.animations[0] if model and model.animations else None
        self.time = 0.0
        if model:
            model.update(self.anim, 0.0)
            lo, hi, r = frame_for(model)
            if not keep:
                self.cam.frame(lo, hi)
            self.grid_y, self.grid_scale = float(lo[1]), max(r / 8, 0.05)
        self.animating = bool(self.anim)

    def tick(self, dt):
        if self.model and self.anim and self.playing:
            self.time = (self.time + dt * self.speed) % max(self.anim.duration, 1e-3)
            self.model.update(self.anim, self.time)
            self.frame_changed.emit(self.time, self.anim.duration)
            return True
        return False

    def seek(self, t: float):
        if self.model and self.anim:
            self.time = t
            self.model.update(self.anim, t)
            self.frame_changed.emit(t, self.anim.duration)
            self.update()

    def draw(self, ctx, w, h):
        ctx.clear(0.105, 0.118, 0.145, 1.0, depth=1.0)
        if self.ren:
            self.ren.render(self.cam.proj(w / max(h, 1)) @ self.cam.view(), self.grid_y, self.grid_scale)

    def reframe(self):
        if self.model:
            lo, hi, _ = frame_for(self.model)
            self.cam.frame(lo, hi)
            self.update()

    def snapshot(self, size: int = 256) -> QImage:
        return self.grabFramebuffer().scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
