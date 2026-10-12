"""Minimal reader for Roblox binary model files (.rbxm): instance classes, names and hierarchy."""
import struct, sys


def lz4_block(src, size):
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        for k in range(ml):
            out.append(out[start + k])
    assert len(out) == size, (len(out), size)
    return bytes(out)


def interleaved_i32(data, pos, count):
    vals = []
    for j in range(count):
        b = bytes(data[pos + i * count + j] for i in range(4))
        x = struct.unpack(">I", b)[0]
        vals.append((x >> 1) ^ -(x & 1))
    return vals, pos + 4 * count


def referents(data, pos, count):
    vals, pos = interleaved_i32(data, pos, count)
    acc = 0
    out = []
    for v in vals:
        acc += v
        out.append(acc)
    return out, pos


def read(path):
    raw = open(path, "rb").read()
    assert raw[:8] == b"<roblox!"
    nclass, ninst = struct.unpack_from("<ii", raw, 16)
    pos = 32
    classes = {}
    inst_class = {}
    props = {}
    parent = {}
    order = []
    while pos < len(raw):
        name = raw[pos:pos + 4]
        clen, ulen = struct.unpack_from("<II", raw, pos + 4)
        pos += 16
        if clen == 0:
            data = raw[pos:pos + ulen]
            pos += ulen
        else:
            comp = raw[pos:pos + clen]
            pos += clen
            if comp[:4] == b"\x28\xb5\x2f\xfd":
                raise RuntimeError("zstd chunk not supported")
            data = lz4_block(comp, ulen)
        if name == b"INST":
            cid, = struct.unpack_from("<I", data, 0)
            ln, = struct.unpack_from("<I", data, 4)
            cname = data[8:8 + ln].decode()
            p = 8 + ln
            fmt = data[p]
            cnt, = struct.unpack_from("<I", data, p + 1)
            refs, p = referents(data, p + 5, cnt)
            classes[cid] = (cname, refs)
            for r in refs:
                inst_class[r] = cname
                order.append(r)
        elif name == b"PROP":
            cid, = struct.unpack_from("<I", data, 0)
            ln, = struct.unpack_from("<I", data, 4)
            pname = data[8:8 + ln].decode()
            p = 8 + ln
            typ = data[p]
            p += 1
            refs = classes[cid][1]
            if typ == 0x01:       # String
                for r in refs:
                    sl, = struct.unpack_from("<I", data, p)
                    props.setdefault(r, {})[pname] = data[p + 4:p + 4 + sl]
                    p += 4 + sl
            elif typ == 0x02:     # Bool
                for k, r in enumerate(refs):
                    props.setdefault(r, {})[pname] = bool(data[p + k])
        elif name == b"PRNT":
            cnt, = struct.unpack_from("<I", data, 1)
            kids, p = referents(data, 5, cnt)
            pars, p = referents(data, p, cnt)
            for k, pa in zip(kids, pars):
                parent[k] = pa
        elif name == b"END\x00":
            break
    return inst_class, props, parent, order


if __name__ == "__main__":
    ic, props, parent, order = read(sys.argv[1])
    kids = {}
    for r in order:
        kids.setdefault(parent.get(r, -1), []).append(r)

    def show(r, d):
        nm = props.get(r, {}).get("Name", b"?").decode("utf-8", "replace")
        print("  " * d + ic[r] + " " + nm)
        for c in kids.get(r, []):
            if d < int(sys.argv[2]) if len(sys.argv) > 2 else True:
                show(c, d + 1)
    for r in kids.get(-1, []):
        show(r, 0)
