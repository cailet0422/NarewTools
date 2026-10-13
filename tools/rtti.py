"""MSVC x64 RTTI walker: finds every vtable via Complete Object Locators,
names slots from exports, and propagates interface (I*) slot names to
implementation (C*) vtables through the class hierarchy."""
import struct, sys, re, json
sys.path.insert(0, __import__('os').path.dirname(__file__))
from pe import PE

def demangle_simple(n):
    # ?Method@Class@Nw@@... -> Class::Method ; ??0Class@Nw -> Class::Class ; ??1 -> ~
    m = re.match(r'\?\?([0-9A-Z_])([A-Za-z_0-9]+)@Nw@@', n)
    if m:
        op, cls = m.groups()
        special = {'0': cls, '1': '~' + cls, '4': 'operator=', '2': 'operator_new', '3': 'operator_delete',
                   '_E': 'vector_dtor', '_G': 'scalar_dtor'}
        return f'{cls}::{special.get(op, "op_" + op)}'
    m = re.match(r'\?([A-Za-z_0-9]+)@([A-Za-z_0-9]+)@Nw@@', n)
    if m:
        return f'{m.group(2)}::{m.group(1)}'
    m = re.match(r'\?([A-Za-z_0-9]+)@Nw@@', n)
    if m:
        return f'Nw::{m.group(1)}'
    return n

def walk(path):
    p = PE(path)
    d = p.d
    base = p.base
    exports = {}
    for name, va, o in p.exports():
        exports.setdefault(va, []).append(name)
    # sections
    rdata = [s for s in p.secs if s[0] == '.rdata'][0]
    text = [s for s in p.secs if s[0] == '.text'][0]
    data = [s for s in p.secs if s[0] == '.data'][0]
    def in_text(va):
        r = va - base
        return text[1] <= r < text[1] + text[2]
    # type descriptors live in .data: vptr, spare, name ".?AV...@@"
    tds = {}
    for m in re.finditer(rb'\.\?A[VU]([A-Za-z_0-9@]+)@@\x00', d):
        off = m.start() - 16
        # convert file off -> rva
        for name, va, vsz, rptr, rsz in p.secs:
            if rptr <= off < rptr + rsz:
                rva = va + off - rptr
                tds[rva] = m.group(1).decode()
    # COLs in .rdata: sig=1, off, cdoff, td_rva, chd_rva, self_rva
    vtables = {}
    r0, rsz = rdata[3], rdata[4]
    for off in range(r0, r0 + rsz - 24, 4):
        sig, o, cdo, td, chd, selfr = struct.unpack_from('<IIIIII', d, off)
        if sig != 1 or td not in tds: continue
        col_rva = rdata[1] + off - r0
        if selfr != col_rva: continue
        # find vtable: a qword in rdata == base+col_rva, vtable starts after it
        needle = struct.pack('<Q', base + col_rva)
        idx = d.find(needle, r0, r0 + rsz)
        while idx != -1:
            vt_off = idx + 8
            vt_va = base + rdata[1] + vt_off - r0
            slots = []
            q = vt_off
            while q < r0 + rsz:
                v = struct.unpack_from('<Q', d, q)[0]
                if not in_text(v): break
                slots.append(v)
                q += 8
                # stop if next qword is another COL pointer (start of next vtable)
            cls = tds[td]
            vtables.setdefault(cls, []).append({'va': vt_va, 'offset': o, 'slots': slots, 'chd': chd})
            idx = d.find(needle, idx + 8, r0 + rsz)
    # class hierarchy descriptors -> base class list
    def bases(chd):
        sig, attr, nb, bca = struct.unpack_from('<IIII', d, p.rva2off(chd))
        out = []
        for i in range(nb):
            bcd = struct.unpack_from('<I', d, p.rva2off(bca) + 4 * i)[0]
            tdr = struct.unpack_from('<I', d, p.rva2off(bcd))[0]
            out.append(tds.get(tdr, '?'))
        return out
    return p, exports, tds, vtables, bases

if __name__ == '__main__':
    path = sys.argv[1]
    p, exports, tds, vtables, bases = walk(path)
    out = {}
    for cls, vts in vtables.items():
        for vt in vts:
            b = bases(vt['chd'])
            own = {x.split('@')[0] for x in b}
            slots = []
            for s in vt['slots']:
                names = [demangle_simple(n) for n in exports.get(s, [])]
                # identical-code folding gives tiny stubs dozens of aliases;
                # keep names from this class hierarchy, else mark the alias count
                mine = [n for n in names if n.split('::')[0] in own]
                if mine:
                    slots.append([hex(s), mine])
                elif names:
                    slots.append([hex(s), [f'<shared stub: {names[0]} +{len(names) - 1}>']])
                else:
                    slots.append([hex(s), []])
            out.setdefault(cls, []).append({
                'vtable': hex(vt['va']), 'this_offset': vt['offset'], 'bases': b,
                'slots': slots})
    json.dump(out, open(sys.argv[2], 'w'), indent=1)
    print('type descriptors', len(tds), 'classes with vtables', len(out))
