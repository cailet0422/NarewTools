"""Sound clips for the sound editor: decode/encode the game's audio and simple edits.

The game's sounds (sound.pack) are WAV (PCM), OGG (Vorbis) and one MP3.  libsndfile (through the
``soundfile`` package) reads and writes all three, so an edited sound is saved in the same format,
sample rate and channel count as the original.  Samples are float32 in [-1, 1], shape
[frames, channels]; every edit returns a new Clip (the editor keeps the old ones for undo).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from .core import StudioError

FORMATS = {'.wav': ('WAV', 'PCM_16'), '.ogg': ('OGG', 'VORBIS'), '.mp3': ('MP3', 'MPEG_LAYER_III')}
EXTS = tuple(FORMATS)


@dataclass
class Clip:
    data: np.ndarray            # float32 [frames, channels]
    rate: int
    subtype: str | None = None  # original sample format (e.g. PCM_16), kept when saving WAV

    @property
    def frames(self) -> int:
        return int(self.data.shape[0])

    @property
    def channels(self) -> int:
        return int(self.data.shape[1])

    @property
    def duration(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def with_data(self, data: np.ndarray) -> 'Clip':
        return Clip(np.ascontiguousarray(data, dtype=np.float32), self.rate, self.subtype)


def decode(data: bytes) -> Clip:
    try:
        with sf.SoundFile(io.BytesIO(data)) as f:
            arr = f.read(dtype='float32', always_2d=True)
            return Clip(arr, f.samplerate, f.subtype)
    except (RuntimeError, sf.LibsndfileError) as ex:
        raise StudioError(f'소리 파일을 읽을 수 없습니다: {ex}')


def encode(clip: Clip, ext: str) -> bytes:
    """Bytes in the format of ext (.wav keeps the clip's PCM subtype, e.g. PCM_16 / PCM_U8)."""
    ext = ext.lower()
    if ext not in FORMATS:
        raise StudioError(f'{ext} 형식으로는 저장할 수 없습니다 (wav, ogg, mp3)')
    fmt, sub = FORMATS[ext]
    if ext == '.wav' and clip.subtype and clip.subtype.startswith(('PCM', 'FLOAT', 'DOUBLE')):
        sub = clip.subtype
    buf = io.BytesIO()
    data = np.clip(clip.data, -1.0, 1.0)
    # in blocks: libsndfile 1.2's Vorbis encoder crashes the process when handed a long clip in one
    # write (12 s of stereo did, 1 s did not); small writes are fine
    with sf.SoundFile(buf, 'w', clip.rate, clip.channels, format=fmt, subtype=sub) as w:
        for i in range(0, len(data), 4096):
            w.write(data[i:i + 4096])
    return buf.getvalue()


def info(data: bytes) -> dict:
    with sf.SoundFile(io.BytesIO(data)) as f:
        return {'rate': f.samplerate, 'channels': f.channels, 'frames': f.frames,
                'format': f.format, 'subtype': f.subtype}


# --------------------------------------------------------------------------- conversions

def resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resampling (good enough for game sound effects)."""
    if src_rate == dst_rate or len(data) == 0:
        return data
    n = max(1, int(round(len(data) * dst_rate / src_rate)))
    x_old = np.arange(len(data), dtype=np.float64)
    x_new = np.linspace(0, len(data) - 1, n)
    return np.stack([np.interp(x_new, x_old, data[:, c]) for c in range(data.shape[1])], axis=1).astype(np.float32)


def remix(data: np.ndarray, channels: int) -> np.ndarray:
    if data.shape[1] == channels:
        return data
    mono = data.mean(axis=1, keepdims=True)
    return np.repeat(mono, channels, axis=1).astype(np.float32)


def conform(clip: Clip, rate: int, channels: int) -> Clip:
    """Same audio at another sample rate / channel count (to replace a game sound in place)."""
    return Clip(remix(resample(clip.data, clip.rate, rate), channels), rate, clip.subtype)


# --------------------------------------------------------------------------- edits on frames [a, b)

def _span(clip: Clip, a: int | None, b: int | None) -> tuple[int, int]:
    a = 0 if a is None else max(0, min(clip.frames, int(a)))
    b = clip.frames if b is None else max(0, min(clip.frames, int(b)))
    return (a, b) if a <= b else (b, a)


def cut(clip: Clip, a, b) -> tuple[Clip, np.ndarray]:
    a, b = _span(clip, a, b)
    return clip.with_data(np.concatenate([clip.data[:a], clip.data[b:]])), clip.data[a:b].copy()


def keep(clip: Clip, a, b) -> Clip:
    a, b = _span(clip, a, b)
    return clip.with_data(clip.data[a:b])


def insert(clip: Clip, at, piece: np.ndarray) -> Clip:
    at, _ = _span(clip, at, at)
    piece = remix(piece, clip.channels)
    return clip.with_data(np.concatenate([clip.data[:at], piece, clip.data[at:]]))


def silence(clip: Clip, a, b) -> Clip:
    a, b = _span(clip, a, b)
    d = clip.data.copy()
    d[a:b] = 0
    return clip.with_data(d)


def fade(clip: Clip, a, b, fade_in: bool) -> Clip:
    a, b = _span(clip, a, b)
    d = clip.data.copy()
    if b > a:
        ramp = np.linspace(0.0, 1.0, b - a, dtype=np.float32)
        d[a:b] *= (ramp if fade_in else ramp[::-1])[:, None]
    return clip.with_data(d)


def gain(clip: Clip, a, b, db: float) -> Clip:
    a, b = _span(clip, a, b)
    d = clip.data.copy()
    d[a:b] *= np.float32(10 ** (db / 20))
    return clip.with_data(np.clip(d, -1.0, 1.0))


def normalize(clip: Clip, a, b, peak_db: float = -1.0) -> Clip:
    a, b = _span(clip, a, b)
    peak = float(np.abs(clip.data[a:b]).max()) if b > a else 0.0
    if peak <= 1e-6:
        return clip
    d = clip.data.copy()
    d[a:b] *= np.float32(10 ** (peak_db / 20) / peak)
    return clip.with_data(d)


def reverse(clip: Clip, a, b) -> Clip:
    a, b = _span(clip, a, b)
    d = clip.data.copy()
    d[a:b] = d[a:b][::-1]
    return clip.with_data(d)


def speed(clip: Clip, factor: float) -> Clip:
    """Play factor x faster (shorter and higher) by resampling at the same rate."""
    if factor <= 0:
        raise StudioError('속도는 0보다 커야 합니다')
    n = max(1, int(round(clip.frames / factor)))
    x_old = np.arange(clip.frames, dtype=np.float64)
    x_new = np.linspace(0, max(0, clip.frames - 1), n)
    return clip.with_data(np.stack([np.interp(x_new, x_old, clip.data[:, c]) for c in range(clip.channels)], axis=1))


# --------------------------------------------------------------------------- display / playback

def peaks(clip: Clip, start: int, frames: int, columns: int) -> tuple[np.ndarray, np.ndarray]:
    """Min and max per pixel column for each channel: arrays [channels, columns]."""
    start = max(0, start)
    end = min(clip.frames, start + max(1, frames))
    seg = clip.data[start:end]
    cols = max(1, columns)
    if len(seg) == 0:
        z = np.zeros((clip.channels, cols), dtype=np.float32)
        return z, z
    idx = np.linspace(0, len(seg), cols + 1).astype(np.int64)
    idx[-1] = len(seg)
    lo = np.zeros((clip.channels, cols), dtype=np.float32)
    hi = np.zeros((clip.channels, cols), dtype=np.float32)
    starts = np.minimum(idx[:-1], len(seg) - 1)
    for c in range(clip.channels):
        ch = seg[:, c]
        lo[c] = np.minimum.reduceat(ch, starts)
        hi[c] = np.maximum.reduceat(ch, starts)
    return lo, hi


def pcm16(clip: Clip, a=None, b=None) -> bytes:
    """Interleaved signed 16-bit samples of [a, b) for the audio device."""
    a, b = _span(clip, a, b)
    return (np.clip(clip.data[a:b], -1.0, 1.0) * 32767).astype('<i2').tobytes()
