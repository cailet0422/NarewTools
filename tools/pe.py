"""Minimal PE helper: sections, exports, VA reads (no external deps)."""
import struct, sys

class PE:
    def __init__(self, path):
        self.d = d = open(path, 'rb').read()
        e = struct.unpack_from('<I', d, 0x3c)[0]
        self.nsec = struct.unpack_from('<H', d, e + 6)[0]
        optsz = struct.unpack_from('<H', d, e + 20)[0]
        opt = e + 24
        self.magic = struct.unpack_from('<H', d, opt)[0]
        self.is64 = self.magic == 0x20b
        self.base = struct.unpack_from('<Q', d, opt + 24)[0] if self.is64 else struct.unpack_from('<I', d, opt + 28)[0]
        ddir = opt + (112 if self.is64 else 96)
        self.dirs = [struct.unpack_from('<II', d, ddir + 8 * i) for i in range(16)]
        self.secs = []
        s = opt + optsz
        for i in range(self.nsec):
            name = d[s:s+8].rstrip(b'\0').decode()
            vsz, va, rsz, rptr = struct.unpack_from('<IIII', d, s + 8)
            self.secs.append((name, va, vsz, rptr, rsz))
            s += 40

    def rva2off(self, rva):
        for name, va, vsz, rptr, rsz in self.secs:
            if va <= rva < va + max(vsz, rsz):
                return rptr + (rva - va)
        return None

    def read(self, va, n):
        off = self.rva2off(va - self.base)
        return self.d[off:off+n]

    def q(self, va):
        return struct.unpack('<Q' if self.is64 else '<I', self.read(va, 8 if self.is64 else 4))[0]

    def cstr(self, va):
        b = self.read(va, 512)
        return b[:b.index(b'\0')].decode('latin1')

    def exports(self):
        rva, size = self.dirs[0]
        if not rva: return []
        o = self.rva2off(rva)
        nfun, nnames, afun, aname, aord = struct.unpack_from('<IIIII', self.d, o + 20)
        base_ord = struct.unpack_from('<I', self.d, o + 16)[0]
        funcs = [struct.unpack_from('<I', self.d, self.rva2off(afun) + 4*i)[0] for i in range(nfun)]
        out = []
        for i in range(nnames):
            nrva = struct.unpack_from('<I', self.d, self.rva2off(aname) + 4*i)[0]
            ordi = struct.unpack_from('<H', self.d, self.rva2off(aord) + 2*i)[0]
            no = self.rva2off(nrva)
            name = self.d[no:self.d.index(b'\0', no)].decode('latin1')
            out.append((name, self.base + funcs[ordi], ordi + base_ord))
        return out

    def imports(self):
        rva, size = self.dirs[1]
        out = []
        if not rva: return out
        o = self.rva2off(rva)
        while True:
            ilt, ts, fwd, nrva, iat = struct.unpack_from('<IIIII', self.d, o)
            if not nrva: break
            dll = self.cstr(self.base + nrva)
            t = self.rva2off(ilt or iat)
            w = 8 if self.is64 else 4
            while True:
                v = struct.unpack_from('<Q' if self.is64 else '<I', self.d, t)[0]
                if not v: break
                if v >> (63 if self.is64 else 31):
                    out.append((dll, '#%d' % (v & 0xffff)))
                else:
                    out.append((dll, self.cstr(self.base + (v & 0x7fffffff) + 2)))
                t += w
            o += 20
        return out
