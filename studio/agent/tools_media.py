"""Sounds (decode, edit, replace keeping the game's format) and models/animations (view, glTF, speed)."""
from __future__ import annotations

import os

import numpy as np

from ..core import StudioError, kind_of
from . import render
from .registry import ARR, BOOL, INT, NUM, Result, STR, tool


class _Audio:
    """studio.audio, imported on first use (it needs the optional soundfile library)."""

    def __getattr__(self, k):
        from .. import audio
        return getattr(audio, k)


A = _Audio()


def _clip(ctx, name, source='auto'):
    return A.decode(ctx.st.read(name, source))


def _frames(clip, t) -> int | None:
    return None if t is None else max(0, min(clip.frames, int(round(float(t) * clip.rate))))


@tool('sound_list', 'sound', 'Game sounds (.wav/.ogg/.mp3) with whether this mod changed them.',
      query=STR('Text in the path.', ''), limit=INT('Max rows.', 300))
def sound_list(ctx, query, limit):
    st = ctx.st
    q = query.lower()
    names = {k: loc.entry.name for k, loc in st.need_game().index.items() if kind_of(k) == 'audio'}
    for k, (n, _) in st.project_files().items():
        if kind_of(k) == 'audio':
            names[k] = n
    rows = [{'name': n, 'status': st.status_of(n)} for k, n in sorted(names.items()) if not q or q in k]
    return {'count': len(rows), 'sounds': rows[:limit]}


@tool('sound_info', 'sound', 'Length, sample rate, channels, peak level of a sound, with a waveform picture.',
      name=STR('Sound entry.'), source=STR('Version.', 'auto', enum=('auto', 'game')),
      waveform=BOOL('Return a waveform picture.', True))
def sound_info(ctx, name, source, waveform):
    c = _clip(ctx, name, source)
    peak = float(np.abs(c.data).max()) if c.frames else 0.0
    data = {'name': ctx.st.canonical_name(name), 'seconds': round(c.duration, 3), 'rate': c.rate,
            'channels': c.channels, 'subtype': c.subtype, 'peak_db': round(20 * np.log10(peak), 2) if peak else None,
            'status': ctx.st.status_of(name)}
    if not waveform:
        return data
    p = render.save(render.waveform(c), ctx.out_path('wave_' + os.path.basename(name)))
    return Result({**data, 'image': p}, [p])


OPS_HELP = ('Ops (times in seconds; start/end default to the whole sound): {"op":"keep","start":a,"end":b} (trim to '
            'a..b), {"op":"cut","start":a,"end":b}, {"op":"silence",...}, {"op":"fade_in","start":a,"end":b}, '
            '{"op":"fade_out",...}, {"op":"gain","db":-6,...}, {"op":"normalize","peak_db":-1,...}, '
            '{"op":"reverse",...}, {"op":"speed","factor":1.2}, {"op":"insert_file","at":t,"path":".."}, '
            '{"op":"append_file","path":".."}, {"op":"replace_file","path":".."} (whole sound from a file).')


def apply_ops(clip, ops: list[dict]):
    for i, op in enumerate(ops):
        k = op.get('op')
        a, b = _frames(clip, op.get('start')), _frames(clip, op.get('end'))
        try:
            if k == 'keep':
                clip = A.keep(clip, a, b)
            elif k == 'cut':
                clip, _ = A.cut(clip, a, b)
            elif k == 'silence':
                clip = A.silence(clip, a, b)
            elif k in ('fade_in', 'fade_out'):
                clip = A.fade(clip, a, b, k == 'fade_in')
            elif k == 'gain':
                clip = A.gain(clip, a, b, float(op['db']))
            elif k == 'normalize':
                clip = A.normalize(clip, a, b, float(op.get('peak_db', -1.0)))
            elif k == 'reverse':
                clip = A.reverse(clip, a, b)
            elif k == 'speed':
                clip = A.speed(clip, float(op['factor']))
            elif k in ('insert_file', 'append_file', 'replace_file'):
                with open(op['path'], 'rb') as f:
                    piece = A.conform(A.decode(f.read()), clip.rate, clip.channels)
                if k == 'replace_file':
                    clip = clip.with_data(piece.data)
                else:
                    at = clip.frames if k == 'append_file' else _frames(clip, op.get('at', 0))
                    clip = A.insert(clip, at, piece.data)
            else:
                raise StudioError(f'모르는 op {k!r}. ' + OPS_HELP)
        except (KeyError, ValueError, OSError) as ex:
            raise StudioError(f'op {i} {op}: {ex}')
    return clip


@tool('sound_edit', 'sound',
      'Edit a game sound with a list of ops and save it to the mod in the original format, rate and channels. '
      + OPS_HELP, writes=True,
      name=STR('Sound entry.'), ops=ARR('Edits in order.', {'type': 'object'}),
      from_original=BOOL('Start from the game original instead of this mod\'s current version.', False),
      dry_run=BOOL('Do not save; return the result\'s waveform.', False))
def sound_edit(ctx, name, ops, from_original, dry_run):
    st = ctx.st
    name = st.canonical_name(name)
    clip = apply_ops(_clip(ctx, name, 'game' if from_original else 'auto'), ops)
    if clip.frames == 0:
        raise StudioError('결과가 빈 소리입니다')
    p = render.save(render.waveform(clip), ctx.out_path('wave_' + os.path.basename(name)))
    out = {'name': name, 'seconds': round(clip.duration, 3), 'rate': clip.rate, 'channels': clip.channels,
           'saved': False, 'image': p}
    if not dry_run:
        st.write(name, A.encode(clip, os.path.splitext(name)[1]))
        ctx.notify([name])
        out['saved'] = True
    return Result(out, [p])


@tool('sound_import', 'sound',
      'Replace a game sound with any audio file (wav/ogg/mp3/flac): converted to the original format, sample rate '
      'and channels, optionally trimmed to start..end seconds.', writes=True,
      name=STR('Sound entry to replace.'), path=STR('Audio file.'),
      start=NUM('Keep from (s).', None), end=NUM('Keep until (s).', None))
def sound_import(ctx, name, path, start, end):
    st = ctx.st
    name = st.canonical_name(name)
    orig = _clip(ctx, name, 'game')
    with open(path, 'rb') as f:
        new = A.conform(A.decode(f.read()), orig.rate, orig.channels)
    new = A.Clip(new.data, new.rate, orig.subtype)        # write with the original's sample format
    if start is not None or end is not None:
        new = A.keep(new, _frames(new, start), _frames(new, end))
    st.write(name, A.encode(new, os.path.splitext(name)[1]))
    ctx.notify([name])
    return {'name': name, 'seconds': round(new.duration, 3), 'rate': new.rate, 'channels': new.channels}


@tool('sound_export', 'sound', 'Write a sound to a file (format from the extension: .wav/.ogg/.mp3).',
      name=STR('Sound entry.'), path=STR('Output file.'), source=STR('Version.', 'auto', enum=('auto', 'game')))
def sound_export(ctx, name, path, source):
    c = _clip(ctx, name, source)
    data = A.encode(c, os.path.splitext(path)[1])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    return {'path': path, 'bytes': len(data)}


# --------------------------------------------------------------------------- models

@tool('models_list', 'models', 'Models (.nwmt) by category (character items, NPCs, objects/furniture, effects ...).',
      query=STR('Text in the path.', ''), category=STR('Only this category.', None), limit=INT('Max rows.', 200))
def models_list(ctx, query, category, limit):
    q = query.lower()
    rows = [m for m in ctx.models.list_models()
            if (not q or q in m['name'].lower()) and (not category or m['category'] == category)]
    cats = sorted({m['category'] for m in rows})
    return {'count': len(rows), 'categories': cats, 'models': rows[:limit]}


@tool('model_info', 'models', 'Bones, meshes, materials of models shown together, their skeleton and animations.',
      names=ARR('Model entries (several = one character: body, face, hair ...).', {'type': 'string'}))
def model_info(ctx, names):
    return ctx.models.info(names)


@tool('model_render', 'models', 'Picture of model(s), optionally posed by an animation at a time.',
      names=ARR('Model entries.', {'type': 'string'}), anims=ARR('Animation entries (.nwat).', {'type': 'string'}, None),
      time=NUM('Animation time in seconds.', 0), yaw=NUM('Camera turn (degrees).', 30),
      pitch=NUM('Camera height angle (degrees).', 15), size=INT('Image size.', 560))
def model_render(ctx, names, anims, time, yaw, pitch, size):
    img, meta = render.model_view(ctx, names, anims or (), time, yaw, pitch, (size, size))
    p = render.save(img, ctx.out_path('model_' + os.path.basename(names[0])))
    return Result({'image': p, **meta}, [p])


@tool('model_export_gltf', 'models', 'Export model(s) (+ animations) to glTF/.glb for Blender (30 fps; export back with '
      'Include > Custom Properties).', names=ARR('Model entries.', {'type': 'string'}),
      path=STR('Output .glb/.gltf.'), anims=ARR('Animation entries.', {'type': 'string'}, None),
      skeleton=STR('Skeleton .nwbt (default: found automatically).', None))
def model_export_gltf(ctx, names, path, anims, skeleton):
    return {'path': ctx.models.export(names, anims or [], skeleton, path)}


@tool('model_import_gltf', 'models',
      'Replace existing model(s) with meshes from an edited glTF (the originals serve as templates).', writes=True,
      names=ARR('Model entries to replace (as exported).', {'type': 'string'}), src=STR('glTF/.glb file.'))
def model_import_gltf(ctx, names, src):
    written, notes = ctx.models.import_models(names, src)
    ctx.notify(written)
    return {'written': written, 'notes': notes}


@tool('model_import_new', 'models', 'Add a new model entry from a glTF (template = similar existing model).',
      writes=True, src=STR('glTF/.glb file.'), target=STR('New entry, e.g. "Object\\my_lamp.nwmt".'),
      template=STR('Template model entry.', None))
def model_import_new(ctx, src, target, template):
    written = ctx.models.import_new_model(src, target, template)
    ctx.notify(written)
    return {'written': written}


@tool('anim_import_gltf', 'models', 'Replace or add an animation (.nwat) from a glTF animation (sampled per frame).',
      writes=True, target=STR('Animation entry (.nwat).'), src=STR('glTF/.glb file.'),
      skeleton=STR('Skeleton .nwbt.', None), animation=STR('Animation name in the glTF (default: first).', None))
def anim_import_gltf(ctx, target, src, skeleton, animation):
    n = ctx.models.import_anim(target, src, skeleton, animation)
    ctx.notify([target])
    return {'target': target, 'frames': n}


@tool('anim_speed', 'models', 'Make an animation play faster/slower in game (factor 2 = twice as fast).',
      writes=True, name=STR('Animation entry (.nwat).'), speed=NUM('Speed factor.'))
def anim_speed(ctx, name, speed):
    if speed <= 0:
        raise StudioError('속도는 0보다 커야 합니다')
    old = ctx.models.anim_framerate(name)
    new, n = ctx.models.set_anim_speed(name, speed)
    ctx.notify([name])
    return {'name': name, 'framerate_before': old, 'framerate_after': new, 'frames': n}
