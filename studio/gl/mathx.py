"""Small matrix helpers (numpy, row-major; upload with .T for GLSL)."""
from __future__ import annotations

import math

import numpy as np


def perspective(fovy_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fovy_deg) / 2)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / max(aspect, 1e-6)
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def look_at(eye, target, up=(0, 1, 0)) -> np.ndarray:
    eye, target, up = (np.asarray(v, dtype=np.float64) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f) or 1
    s = np.cross(f, up)
    s /= np.linalg.norm(s) or 1
    u = np.cross(s, f)
    m = np.identity(4, dtype=np.float32)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[0, 3], m[1, 3], m[2, 3] = -s @ eye, -u @ eye, f @ eye
    return m


def translate(x, y, z) -> np.ndarray:
    m = np.identity(4, dtype=np.float32)
    m[:3, 3] = (x, y, z)
    return m


def scale(x, y, z) -> np.ndarray:
    return np.diag([x, y, z, 1]).astype(np.float32)


def quat_to_mat(q) -> np.ndarray:
    x, y, z, w = q
    m = np.identity(4, dtype=np.float32)
    m[0, :3] = (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w))
    m[1, :3] = (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w))
    m[2, :3] = (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y))
    return m


def trs(t, r, s) -> np.ndarray:
    return translate(*t) @ quat_to_mat(r) @ scale(*s)


def slerp(a: np.ndarray, b: np.ndarray, u: float) -> np.ndarray:
    a = a / (np.linalg.norm(a) or 1)
    b = b / (np.linalg.norm(b) or 1)
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        r = a + u * (b - a)
        return r / (np.linalg.norm(r) or 1)
    th = math.acos(min(1.0, d))
    return (math.sin((1 - u) * th) * a + math.sin(u * th) * b) / math.sin(th)


class OrbitCamera:
    """Yaw/pitch/distance around a target."""

    def __init__(self):
        self.target = np.array([0.0, 0.0, 0.0])
        self.yaw = math.radians(35)
        self.pitch = math.radians(25)
        self.dist = 4.0
        self.fov = 45.0
        self.near, self.far = 0.05, 2000.0

    @property
    def eye(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        d = np.array([math.sin(self.yaw) * cp, math.sin(self.pitch), math.cos(self.yaw) * cp])
        return self.target + d * self.dist

    def forward_flat(self) -> np.ndarray:
        f = self.target - self.eye
        f[1] = 0
        n = np.linalg.norm(f)
        return f / n if n else np.array([0.0, 0.0, -1.0])

    def view(self) -> np.ndarray:
        return look_at(self.eye, self.target)

    def proj(self, aspect: float) -> np.ndarray:
        return perspective(self.fov, aspect, self.near, self.far)

    def rotate(self, dx: float, dy: float):
        self.yaw -= dx * 0.008
        self.pitch = max(-1.5, min(1.5, self.pitch + dy * 0.008))

    def pan(self, dx: float, dy: float, height_px: float):
        k = 2 * self.dist * math.tan(math.radians(self.fov) / 2) / max(height_px, 1)
        v = self.view()
        right, up = v[0, :3], v[1, :3]
        self.target = self.target + (-dx * right + dy * up) * k

    def zoom(self, steps: float):
        self.dist = max(0.05, min(5000.0, self.dist * (0.88 ** steps)))

    def frame(self, lo, hi):
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        self.target = (lo + hi) / 2
        r = float(np.linalg.norm(hi - lo)) / 2 or 1.0
        self.dist = r / math.tan(math.radians(self.fov) / 2) * 1.1
        self.near, self.far = max(r / 500, 0.01), r * 200 + 100

    def ray(self, px: float, py: float, w: float, h: float) -> tuple[np.ndarray, np.ndarray]:
        """World-space ray through a pixel (origin, unit direction)."""
        x = 2 * px / max(w, 1) - 1
        y = 1 - 2 * py / max(h, 1)
        inv = np.linalg.inv(self.proj(w / max(h, 1)) @ self.view())
        a = inv @ np.array([x, y, -1, 1])
        b = inv @ np.array([x, y, 1, 1])
        a, b = a[:3] / a[3], b[:3] / b[3]
        d = b - a
        return a, d / (np.linalg.norm(d) or 1)
