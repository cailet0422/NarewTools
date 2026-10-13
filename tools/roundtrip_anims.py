"""Round-trip check for animations: .nwat (+ its .nwbt) -> .glb -> .nwat.

    python tools/roundtrip_anims.py FILE.nwat [...]
    python tools/roundtrip_anims.py --all D:\\isletre\\extracted

Both files are evaluated the way the engine does (IAnimationKey::GetPos @0x180065420:
key index = frame, clamped to the last key) at every frame, per bone track; positions and
scales must agree to 1e-5, rotations to 2e-5 (q and -q are the same rotation).  Animations
without a matching skeleton, or with mesh tracks only, are reported as skipped.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import tempfile
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from narew.convert import export_gltf, import_animation, _el_to_str  # noqa: E402
from narew.nwmodel import guess_skeleton, load_animation  # noqa: E402
from narew.nwxml import parse_file  # noqa: E402
import validate_gltf  # noqa: E402


def engine_array(keys):
    if not keys:
        return []
    count = len(keys)
    arr = [None] * count
    for n, v in keys:
        if 0 <= n < count:              # IAnimationKey::SetPos ignores out-of-range indices (inferred)
            arr[n] = v
    return arr


def at(arr, f):
    return arr[min(f, len(arr) - 1)] if arr else None


def compare(a, b) -> list[str]:
    errs = []
    if (a.framerate, a.start, a.end) != (b.framerate, b.start, b.end):
        errs.append(f'frame header {(a.framerate, a.start, a.end)} != {(b.framerate, b.start, b.end)}')
    tb = {t.id: t for t in b.bones}
    nframes = max([len(t.pos) for t in a.bones] + [len(t.rot) for t in a.bones] + [1])
    worst = {'pos': 0.0, 'rot': 0.0, 'scale': 0.0}
    for t in a.bones:
        u = tb.get(t.id)
        if u is None:
            errs.append(f'bone {t.id}: missing')
            continue
        for kind in ('pos', 'rot', 'scale'):
            ka, kb = engine_array(getattr(t, kind)), engine_array(getattr(u, kind))
            if not ka:
                continue
            for f in range(nframes):
                x, y = at(ka, f), at(kb, f)
                if x is None:
                    continue                    # never set in the original (n starting at 1)
                if y is None:
                    worst[kind] = float('inf')
                    continue
                d = max(abs(p - q) for p, q in zip(x, y))
                if kind == 'rot':
                    d = min(d, max(abs(p + q) for p, q in zip(x, y)))
                worst[kind] = max(worst[kind], d)
    for kind, tol in (('pos', 1e-5), ('rot', 2e-5), ('scale', 1e-5)):
        if worst[kind] > tol:
            errs.append(f'{kind} max diff {worst[kind]:g}')
    ma = {t.id: t for t in a.meshes}
    mb = {t.id: t for t in b.meshes}
    if set(ma) != set(mb):
        errs.append(f'mesh tracks {sorted(ma)} != {sorted(mb)}')
    ea = {tag: _el_to_str(e) for tag, e in a.extra}
    eb = {tag: _el_to_str(e) for tag, e in b.extra}
    if ea != eb:
        errs.append(f'extra sections differ: {sorted(set(ea) ^ set(eb)) or "content"}')
    return errs


def run_one(path):
    try:
        hdr = parse_file(path).find('header')
        nb = int(hdr.get('bone', 0)) if hdr is not None else 0
        if nb == 0:
            return path, None, 'no bone tracks'
        skel = guess_skeleton(path, nb)
        if not skel:
            return path, None, 'no matching .nwbt'
        with tempfile.TemporaryDirectory(prefix='nwat_') as tmp, contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            glb = os.path.join(tmp, 'a.glb')
            back = os.path.join(tmp, 'a.nwat')
            export_gltf([skel], glb, anims=[path], helpers=False)
            rep = validate_gltf.validate(glb)
            import_animation(glb, back)
            errs = compare(load_animation(path), load_animation(back))
            errs += [f'glTF: {e}' for e in rep.errors[:3]]
        return path, errs, None
    except Exception as ex:  # report, keep going
        return path, [f'exception: {ex!r}'], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--all', metavar='ROOT')
    ap.add_argument('-j', '--jobs', type=int, default=os.cpu_count())
    a = ap.parse_args()
    files = list(a.files)
    if a.all:
        for dp, _, fns in os.walk(a.all):
            files += [os.path.join(dp, f) for f in fns if f.lower().endswith('.nwat')]
    ok = failed = 0
    skipped = {}
    with Pool(min(a.jobs, max(1, len(files)))) as pool:
        for path, errs, why in pool.imap_unordered(run_one, files, chunksize=2):
            if errs is None:
                skipped[why] = skipped.get(why, 0) + 1
            elif errs:
                failed += 1
                if failed <= 25:
                    print(f'FAIL {path}')
                    for e in errs[:4]:
                        print('    ', e)
            else:
                ok += 1
                if len(files) <= 10:
                    print(f'OK   {path}')
    print(f'{ok} ok, {failed} failed, skipped: {skipped or 0}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
