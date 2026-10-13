"""Round-trip check: .nwmt -> .glb -> .nwmt, compared numerically with the original.

    python tools/roundtrip_models.py FILE.nwmt [...]          # specific files
    python tools/roundtrip_models.py --all D:\\isletre\\extracted  # every .nwmt (parallel)

Per mesh (matched by kind + id) it compares name, material id, parent, transform, bound box,
positions, normals, tangents, UVs, faces and skin influences, plus material XML and every
passthrough section.  Also reports how many text lines differ from the original file.
The .glb is validated with tools/validate_gltf.py rules.  Exit code 1 if anything fails.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from narew.convert import export_gltf, import_model, _el_to_str  # noqa: E402
from narew.nwmodel import load_model  # noqa: E402
from narew.nwxml import decode  # noqa: E402
import validate_gltf  # noqa: E402

TOL = {'positions': 1e-6, 'normals': 1e-6, 'tangents': 1e-6, 'uvs': 1e-6, 'transform': 1e-9,
       'weights': 2e-5, 'bound_box': 1e-5}


def maxdiff(a, b, f32=False):
    """Largest |a-b|.  With f32, differences explained by float32 storage are discounted:
    a value printed with 5 decimals can only change once its float32 spacing reaches the
    print step, i.e. for |v| >= 64 (unnormalised tangents of up to 28,000 exist)."""
    if a is None or b is None:
        return 0.0 if a is None and b is None else float('inf')
    if len(a) != len(b):
        return float('inf')
    m = 0.0
    for x, y in zip(a, b):
        for p, q in zip(x, y):
            d = abs(p - q)
            if f32 and abs(p) >= 64:
                d = max(0.0, d - 1.0000001e-5 - 6e-8 * abs(p))
            if d > m:
                m = d
    return m


def compare(orig, back) -> list[str]:
    errs = []
    ka = {(m.kind, m.id): m for m in orig.meshes}
    kb = {(m.kind, m.id): m for m in back.meshes}
    if set(ka) != set(kb):
        errs.append(f'mesh sets differ {sorted(ka)} vs {sorted(kb)}')
    for k in sorted(set(ka) & set(kb)):
        a, b = ka[k], kb[k]
        tag = f'{k[0]} {k[1]} {a.name!r}'
        for attr in ('name', 'material', 'parent_id', 'parent_name', 'use_bone'):
            if getattr(a, attr) != getattr(b, attr):
                errs.append(f'{tag}: {attr} {getattr(a, attr)!r} != {getattr(b, attr)!r}')
        if a.faces != b.faces:
            errs.append(f'{tag}: faces differ ({len(a.faces)} vs {len(b.faces)})')
        for attr in ('positions', 'normals', 'tangents', 'uvs'):
            d = maxdiff(getattr(a, attr), getattr(b, attr), f32=True)
            if d > TOL[attr]:
                errs.append(f'{tag}: {attr} max diff {d:g}')
        d = maxdiff(a.transform, b.transform)
        if d > TOL['transform']:
            errs.append(f'{tag}: transform max diff {d:g}')
        if a.bound_box and b.bound_box and maxdiff([a.bound_box], [b.bound_box]) > TOL['bound_box']:
            errs.append(f'{tag}: bound_box differs')
        if (a.skin is None) != (b.skin is None):
            errs.append(f'{tag}: skin presence differs')
        elif a.skin:
            bones_bad = sum(1 for x, y in zip(a.skin, b.skin)
                            for (ba, wa), (bb, wb) in zip(x, y) if ba != bb and (wa > 0 or wb > 0))
            wsum_a = [sum(w for _, w in x) for x in a.skin]
            wd = 0.0
            for x, y, s in zip(a.skin, b.skin, wsum_a):
                for (_, wa), (_, wb) in zip(x, y):
                    ref = wa / s if s > 0 else wa      # export normalises the 4 weights
                    wd = max(wd, abs(ref - wb))
            if bones_bad:
                errs.append(f'{tag}: {bones_bad} skin bone index mismatches')
            if wd > TOL['weights']:
                errs.append(f'{tag}: skin weight max diff {wd:g}')
    ma = {m.id: _el_to_str(m.element) for m in orig.materials}
    mb = {m.id: _el_to_str(m.element) for m in back.materials}
    used = {m.material for m in orig.meshes}
    for mid in used:
        if mid in ma and ma.get(mid) != mb.get(mid):
            errs.append(f'material {mid} differs')
    ra = {t: _el_to_str(e) for t, e in orig.sections if e is not None}
    rb = {t: _el_to_str(e) for t, e in back.sections if e is not None}
    if ra != rb:
        errs.append(f'passthrough sections differ: {sorted(set(ra) ^ set(rb)) or "content"}')
    return errs


def text_diff(p1, p2) -> tuple[int, int]:
    with open(p1, 'rb') as f:
        a = decode(f.read()).splitlines()
    with open(p2, 'rb') as f:
        b = decode(f.read()).splitlines()
    n = sum(1 for x, y in zip(a, b) if x.strip() != y.strip()) + abs(len(a) - len(b))
    return n, len(a)


def run_one(path: str, tmp: str | None = None, validate=True):
    tmpdir = tmp or tempfile.mkdtemp(prefix='nwrt_')
    stem = os.path.splitext(os.path.basename(path))[0]
    glb = os.path.join(tmpdir, stem + '.glb')
    back = os.path.join(tmpdir, stem + '.back.nwmt')
    try:
        export_gltf([path], glb, embed_textures=False)
        rep = validate_gltf.validate(glb) if validate else None
        import_model(glb, back, write_textures=False)
        errs = compare(load_model(path), load_model(back))
        if rep is not None:
            errs += [f'glTF: {e}' for e in rep.errors[:5]]
        nd, nl = text_diff(path, back)
        return path, errs, nd, nl
    except Exception as ex:  # report, keep going
        import traceback
        return path, [f'exception: {ex!r} {traceback.format_exc().splitlines()[-3].strip()}'], -1, 0
    finally:
        if tmp is None:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)


def _quiet_run(path):
    import io
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):
        return run_one(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--all', metavar='ROOT')
    ap.add_argument('-j', '--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--keep', metavar='DIR', help='keep the .glb/.nwmt outputs here (single files)')
    a = ap.parse_args()
    files = list(a.files)
    if a.all:
        for dp, _, fns in os.walk(a.all):
            files += [os.path.join(dp, f) for f in fns if f.lower().endswith('.nwmt')]
    failed = 0
    identical = 0
    total_lines = diff_lines = 0
    if len(files) > 1:
        with Pool(a.jobs) as pool:
            results = pool.imap_unordered(_quiet_run, files, chunksize=2)
            for i, (path, errs, nd, nl) in enumerate(results):
                if errs:
                    failed += 1
                    if failed <= 25:
                        print(f'FAIL {path}')
                        for e in errs[:4]:
                            print('    ', e)
                identical += nd == 0
                total_lines += nl
                diff_lines += max(nd, 0)
                if (i + 1) % 500 == 0:
                    print(f'  {i + 1}/{len(files)}', file=sys.stderr)
    else:
        if a.keep:
            os.makedirs(a.keep, exist_ok=True)
        for path in files:
            path, errs, nd, nl = run_one(path, a.keep)
            print(f'{"FAIL" if errs else "OK  "} {path}: {nd} of {nl} text lines differ')
            for e in errs:
                print('    ', e)
            failed += bool(errs)
            identical += nd == 0
            total_lines += nl
            diff_lines += max(nd, 0)
    print(f'{len(files) - failed}/{len(files)} files round-trip within tolerance; '
          f'{identical} text-identical; {diff_lines} of {total_lines} lines differ overall')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
