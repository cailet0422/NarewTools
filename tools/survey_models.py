"""Survey the XML schema of every .nwmt/.nwbt/.nwat file under an extracted folder.

    python tools/survey_models.py D:\\isletre\\extracted -o re/model-survey.json

For every element path (top-level tags after the synthetic root, list items kept by tag) it
records how many files/elements contain it, every attribute with value statistics (numeric
min/max per token position, token counts, or distinct string samples) and text samples.
It also runs format-specific consistency checks (list lengths vs counts, skin influences,
key counts vs frame range, texture path resolution, matrix affinity/handedness).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from narew.nwxml import floats, ival, parse_file, read_tm  # noqa: E402

EXTS = ('.nwmt', '.nwbt', '.nwat')
MAX_SAMPLES = 25


def _num(tok: str):
    try:
        v = float(tok)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


class Stats:
    def __init__(self):
        # path -> {'files': n, 'elems': n, 'attrs': {name: {...}}, 'text': {...}}
        self.paths: dict = {}
        self.checks: dict = defaultdict(Counter)
        self.samples: dict = defaultdict(list)
        self.ranges: dict = {}

    # -- generic
    def _attr_slot(self, path, name):
        p = self.paths.setdefault(path, {'files': 0, 'elems': 0, 'attrs': {}, 'text': None})
        return p['attrs'].setdefault(name, {'n': 0, 'ntok': Counter(), 'min': [], 'max': [],
                                            'strings': Counter(), 'nstr': 0})

    def add_value(self, slot, value: str):
        slot['n'] += 1
        toks = value.split()
        nums = [_num(t) for t in toks]
        if toks and all(v is not None for v in nums):
            slot['ntok'][len(nums)] += 1
            for i, v in enumerate(nums[:16]):
                if i >= len(slot['min']):
                    slot['min'].append(v)
                    slot['max'].append(v)
                else:
                    if v < slot['min'][i]:
                        slot['min'][i] = v
                    if v > slot['max'][i]:
                        slot['max'][i] = v
        else:
            slot['nstr'] += 1
            if value in slot['strings'] or len(slot['strings']) < 200:
                slot['strings'][value] += 1

    def walk(self, root, ext):
        seen = set()

        def rec(e, path):
            p = self.paths.setdefault(path, {'files': 0, 'elems': 0, 'attrs': {}, 'text': None})
            p['elems'] += 1
            if path not in seen:
                seen.add(path)
                p['files'] += 1
            for k, v in e.attrib.items():
                self.add_value(self._attr_slot(path, k), v)
            if e.text and e.text.strip():
                self.add_value(self._attr_slot(path, '#text'), e.text.strip())
            elif len(e) == 0 and e.text is not None:
                self.add_value(self._attr_slot(path, '#text'), '')
            for c in e:
                rec(c, path + '/' + c.tag)

        for c in root:
            rec(c, ext + ':' + c.tag)

    def check(self, key, cond, sample=None):
        self.checks[key]['ok' if cond else 'FAIL'] += 1
        if not cond and sample is not None and len(self.samples[key]) < 8:
            self.samples[key].append(sample)

    def count(self, key, value, sample=None):
        self.checks[key][str(value)] += 1
        if sample is not None:
            sk = f'{key}={value}'
            if len(self.samples[sk]) < 4:
                self.samples[sk].append(sample)

    def rng(self, key, v):
        r = self.ranges.get(key)
        if r is None:
            self.ranges[key] = [v, v]
        else:
            if v < r[0]:
                r[0] = v
            if v > r[1]:
                r[1] = v

    # -- merge
    def merge(self, o: 'Stats'):
        for path, p in o.paths.items():
            q = self.paths.setdefault(path, {'files': 0, 'elems': 0, 'attrs': {}, 'text': None})
            q['files'] += p['files']
            q['elems'] += p['elems']
            for name, s in p['attrs'].items():
                t = q['attrs'].setdefault(name, {'n': 0, 'ntok': Counter(), 'min': [], 'max': [],
                                                 'strings': Counter(), 'nstr': 0})
                t['n'] += s['n']
                t['nstr'] += s['nstr']
                t['ntok'].update(s['ntok'])
                for i, (lo, hi) in enumerate(zip(s['min'], s['max'])):
                    if i >= len(t['min']):
                        t['min'].append(lo)
                        t['max'].append(hi)
                    else:
                        t['min'][i] = min(t['min'][i], lo)
                        t['max'][i] = max(t['max'][i], hi)
                for k, v in s['strings'].items():
                    if k in t['strings'] or len(t['strings']) < 200:
                        t['strings'][k] += v
        for k, c in o.checks.items():
            self.checks[k].update(c)
        for k, lst in o.samples.items():
            room = 8 - len(self.samples[k])
            if room > 0:
                self.samples[k].extend(lst[:room])
        for k, (lo, hi) in o.ranges.items():
            self.rng(k, lo)
            self.rng(k, hi)


def det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def check_tm(st, key, tm, rel):
    if tm is None:
        st.count(key + '.present', False, rel)
        return
    affine = (abs(tm[0][3]) < 1e-6 and abs(tm[1][3]) < 1e-6 and abs(tm[2][3]) < 1e-6
              and abs(tm[3][3] - 1) < 1e-6)
    st.check(key + '.affine(col3=0001)', affine, rel)
    d = det3(tm)
    st.count(key + '.det_sign', '+' if d > 0 else ('-' if d < 0 else '0'), rel)
    st.rng(key + '.det', d)


def check_texture(st, kind, text, folder, rel):
    t = (text or '').strip()
    if not t:
        st.count(f'texture.{kind}', 'empty')
        return
    if t.startswith(':\\') or (len(t) > 2 and t[1] == ':'):
        base = os.path.basename(t.replace('\\', '/'))
        cls = 'absolute'
    elif '\\' in t or '/' in t:
        base = t.replace('\\', '/')
        cls = 'relative-subpath'
    else:
        base = t
        cls = 'relative'
    exists = os.path.exists(os.path.join(folder, base))
    st.count(f'texture.{kind}', f'{cls}/{"found" if exists else "missing"}',
             f'{rel} -> {t}' if not exists else None)
    st.count('texture.ext', os.path.splitext(base)[1].lower())


def check_vertex(st, kind, m, rel):
    v = m.find('vertex')
    if v is None:
        st.count(f'{kind}.vertex', 'absent', rel)
        return
    nv = ival(v, 'count')
    lists = tuple(c.tag for c in v)
    st.count(f'{kind}.vertex.lists', ','.join(lists), rel)
    for c in v:
        items = list(c)
        if c.tag == 'skin_list':
            per = Counter(ival(s, 'vert') for s in items)
            st.count(f'{kind}.skin.entries_per_vertex', ','.join(map(str, sorted(set(per.values())))), rel)
            st.check(f'{kind}.skin.every_vertex_has_entries', len(per) == nv, rel)
            wsum = defaultdict(float)
            bones = set()
            nmax = 0
            for s in items:
                w = float(s.get('weight'))
                wsum[ival(s, 'vert')] += w
                nmax = max(nmax, ival(s, 'n'))
                if w > 0:
                    bones.add(ival(s, 'bone'))
                st.rng(f'{kind}.skin.weight', w)
                st.rng(f'{kind}.skin.bone', ival(s, 'bone'))
            for k, s in wsum.items():
                st.rng(f'{kind}.skin.weight_sum', s)
            st.check(f'{kind}.skin.weight_sum~1 (±0.01)', all(abs(s - 1) < 0.01 for s in wsum.values()), rel)
            ub = m.get('use_bone')
            if ub is not None:
                st.count(f'{kind}.use_bone vs distinct weighted bones',
                         'equal' if int(ub) == len(bones) else ('use_bone>' if int(ub) > len(bones) else 'use_bone<'),
                         f'{rel} use_bone={ub} distinct={len(bones)}')
            # order of n within a vertex: sorted by weight?
            byv = defaultdict(list)
            for s in items:
                byv[ival(s, 'vert')].append((ival(s, 'n'), float(s.get('weight'))))
            desc = all([w for _, w in sorted(lst)] == sorted([w for _, w in lst], reverse=True)
                       for lst in byv.values())
            st.count(f'{kind}.skin.weights_sorted_desc', desc)
            continue
        st.check(f'{kind}.vertex.{c.tag}.len==count', len(items) == nv, f'{rel} {len(items)} vs {nv}')
        ns = [ival(i, 'n') for i in items]
        st.check(f'{kind}.vertex.{c.tag}.n==position', ns == list(range(len(items))), rel)
        if c.tag == 'tangent_list':
            for i in items:
                t = floats(i.get('value'))
                st.rng(f'{kind}.tangent.w', t[3] if len(t) > 3 else float('nan'))
                st.rng(f'{kind}.tangent.len', math.sqrt(sum(x * x for x in t[:3])))
        elif c.tag == 'normal_list':
            for i in items[:2000]:
                t = floats(i.get('value'))
                st.rng(f'{kind}.normal.len', math.sqrt(sum(x * x for x in t[:3])))
        elif c.tag in ('uv_list', 'uv2_list', 'lightmap_uv_list'):
            for i in items[:5000]:
                t = floats(i.get('value'))
                st.count(f'{kind}.{c.tag}.components', len(t), rel if len(t) != 2 else None)
                st.rng(f'{kind}.{c.tag}.u', t[0])
                if len(t) > 1:
                    st.rng(f'{kind}.{c.tag}.v', t[1])
    idx = m.find('index')
    if idx is not None:
        faces = list(idx)
        st.check(f'{kind}.index.count==faces', ival(idx, 'count') == len(faces), rel)
        st.count(f'{kind}.index.b32bit', idx.get('b32bit'))
        mx = -1
        for f_ in faces:
            mx = max(mx, ival(f_, 'a'), ival(f_, 'b'), ival(f_, 'c'))
        st.check(f'{kind}.index.max<vertex.count', mx < nv, f'{rel} max={mx} nv={nv}')
        st.check(f'{kind}.index.b32bit consistent with nv>65535',
                 (idx.get('b32bit') == '1') == (nv > 65535) or nv <= 65535, rel)
        st.rng(f'{kind}.vertex.count', nv)
        st.rng(f'{kind}.face.count', len(faces))


def check_anim_lists(st, root, fr, rel):
    start = ival(fr, 'start') if fr is not None else 0
    end = ival(fr, 'end') if fr is not None else 0
    nframes = end - start + 1
    for lst in root:
        if not lst.tag.endswith('animation_list'):
            continue
        for a in lst:
            for k in a:
                if not k.tag.endswith('_key'):
                    continue
                n = len(k)
                st.check(f'anim.{k.tag}.count==children', ival(k, 'count') == n, rel)
                ns = [ival(x, 'n') for x in k]
                st.check(f'anim.{k.tag}.n==0..count-1', ns == list(range(n)), rel)
                if n > 1:
                    rel_ = 'n==end-start+1' if n == nframes else (
                        'n==end-start' if n == nframes - 1 else (
                            'n==end-start-1' if n == nframes - 2 else 'other'))
                    st.count(f'anim.{k.tag} count vs frames (multi-key tracks)', rel_,
                             f'{rel} count={n} start={start} end={end}')
                st.count(f'anim.{k.tag}.count', 'one' if n == 1 else ('zero' if n == 0 else 'many'))
                if k.tag == 'rot_key':
                    for x in k:
                        q = floats(x.get('value'))
                        st.rng('anim.rot.len', math.sqrt(sum(c * c for c in q)))


def survey_file(path_rel):
    try:
        return _survey_file(path_rel)
    except Exception as ex:  # keep going, report where
        import traceback
        st = Stats()
        st.count('survey', 'exception', f'{path_rel[1]}: {ex!r} {traceback.format_exc().splitlines()[-3].strip()}')
        return st


def _survey_file(path_rel):
    path, rel = path_rel
    st = Stats()
    ext = os.path.splitext(path)[1].lower()
    try:
        root = parse_file(path)
    except Exception as ex:  # report and continue
        st.count('parse', 'error', f'{rel}: {ex}')
        return st
    st.count('parse', 'ok')
    st.count(f'{ext}.flavour', root.get('wrapped', 'multi-root'))
    st.count(f'{ext}.toplevel', ','.join(c.tag for c in root))
    st.walk(root, ext)
    folder = os.path.dirname(path)
    hdr = root.find('header')
    fr = hdr.find('frame') if hdr is not None else None
    if hdr is not None:
        for k, v in hdr.attrib.items():
            # does the header count match the list length?
            lst = root.find(f'{k}_list') if k not in ('bone',) else (root.find('bonelist') or root.find('bone_list'))
            if k == 'bone' and ext == '.nwat':
                lst = root.find('bone_animation_list')
            if k == 'mesh' and ext == '.nwat':
                lst = root.find('mesh_animation_list')
            if lst is not None:
                st.check(f'{ext}.header.{k}==len({lst.tag})', int(v) == len(lst), f'{rel} {v} vs {len(lst)}')
    for lst in root:
        if lst.get('count') is not None and lst.tag.endswith(('_list', 'list')):
            st.check(f'{ext}.{lst.tag}.count==children', ival(lst, 'count') == len(lst), rel)
    for mat in root.iter('material'):
        for c in mat:
            if c.tag.endswith('_map'):
                check_texture(st, c.tag, c.text, folder, rel)
    for kind in ('mesh', 'skinmesh'):
        lst = root.find(f'{kind}_list')
        if lst is None:
            continue
        for m in lst:
            check_vertex(st, kind, m, rel)
            check_tm(st, f'{kind}.transform', read_tm(m.find('transform')), rel)
            par = m.find('parent')
            st.count(f'{kind}.parent', 'root' if par is not None and par.get('id') == '-1' else 'other',
                     rel if par is not None and par.get('id') != '-1' else None)
            st.count(f'{kind}.material_ref', 'none' if m.get('material') in (None, '-1') else 'set')
    for b in root.iter('bone'):
        if b.find('transform') is not None:
            check_tm(st, f'{ext}.bone.transform', read_tm(b.find('transform')), rel)
        ik2 = b.find('init_key2')
        if ik2 is not None:
            st.count('bone.init_key2', 'empty' if len(ik2) == 0 and not ik2.attrib else 'nonempty', rel)
    if ext == '.nwat' or root.find('mesh_animation_list') is not None or root.find('bone_animation_list') is not None:
        check_anim_lists(st, root, fr, rel)
    if fr is not None:
        st.rng(f'{ext}.frame.end-start', ival(fr, 'end') - ival(fr, 'start'))
    return st


def to_jsonable(st: Stats):
    paths = {}
    for path in sorted(st.paths):
        p = st.paths[path]
        attrs = {}
        for name, s in p['attrs'].items():
            d = {'n': s['n']}
            if s['ntok']:
                d['tokens'] = dict(sorted(s['ntok'].items()))
                d['min'] = s['min']
                d['max'] = s['max']
            if s['nstr']:
                d['strings'] = s['nstr']
                d['distinct_strings(capped)'] = len(s['strings'])
                d['samples'] = [k for k, _ in s['strings'].most_common(MAX_SAMPLES)]
            attrs[name] = d
        paths[path] = {'files': p['files'], 'elems': p['elems'], 'attrs': attrs}
    return {'paths': paths,
            'checks': {k: dict(v) for k, v in sorted(st.checks.items())},
            'ranges': dict(sorted(st.ranges.items())),
            'samples': {k: v for k, v in sorted(st.samples.items())}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('-o', '--out', default='model-survey.json')
    ap.add_argument('-j', '--jobs', type=int, default=os.cpu_count())
    a = ap.parse_args()
    files = []
    for dp, _, fns in os.walk(a.root):
        for fn in fns:
            if fn.lower().endswith(EXTS):
                full = os.path.join(dp, fn)
                files.append((full, os.path.relpath(full, a.root)))
    files.sort(key=lambda x: -os.path.getsize(x[0]))
    total = Stats()
    with Pool(a.jobs) as pool:
        for i, st in enumerate(pool.imap_unordered(survey_file, files, chunksize=4)):
            total.merge(st)
            if (i + 1) % 500 == 0:
                print(f'{i + 1}/{len(files)}', file=sys.stderr)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(to_jsonable(total), f, ensure_ascii=False, indent=1)
    print(f'surveyed {len(files)} files -> {a.out}')


if __name__ == '__main__':
    main()
