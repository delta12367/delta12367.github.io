"""A tree-walking Lua (Luau subset) interpreter in Python with hooks for symbolic execution."""
import math, struct
from luaparse import Node


class LuaError(Exception):
    def __init__(self, value, tb=None):
        super().__init__(value)
        self.value = value


class LuaTable:
    __slots__ = ("h", "meta", "name", "dead", "__weakref__")

    def __init__(self, h=None, name=None):
        self.h = h if h is not None else {}
        self.meta = None
        self.name = name
        self.dead = None

    def get(self, k):
        if isinstance(k, float) and k.is_integer():
            pass
        return self.h.get(k)

    def length(self):
        h = self.h
        if 1.0 not in h:
            return 0
        # binary-ish border search
        n = 1
        while (n * 2) in h:
            n *= 2
        lo, hi = n, n * 2
        while hi - lo > 1:
            m = (lo + hi) // 2
            if m in h:
                lo = m
            else:
                hi = m
        return lo

    def __repr__(self):
        return f"<table {self.name or hex(id(self))}>"


class LuaFunction:
    __slots__ = ("node", "scope", "name")

    def __init__(self, node, scope, name=None):
        self.node = node
        self.scope = scope
        self.name = name

    def __repr__(self):
        return f"<function {self.name or ''} line {self.node.line}>"


class Builtin:
    __slots__ = ("fn", "name")

    def __init__(self, fn, name):
        self.fn = fn
        self.name = name

    def __repr__(self):
        return f"<builtin {self.name}>"


class Buffer:
    __slots__ = ("b",)

    def __init__(self, n):
        self.b = bytearray(n)

    def __repr__(self):
        return f"<buffer {len(self.b)}>"


class Scope:
    __slots__ = ("vars", "parent", "varargs")

    def __init__(self, parent=None, varargs=None):
        self.vars = {}
        self.parent = parent
        self.varargs = varargs if varargs is not None else (parent.varargs if parent else [])

    def lookup(self, name):
        s = self
        while s is not None:
            v = s.vars
            if name in v:
                return s
            s = s.parent
        return None


BREAK = ("break",)
CONTINUE = ("continue",)


def key_norm(k):
    if isinstance(k, float) and k.is_integer() and k == k:
        return k
    if isinstance(k, int) and not isinstance(k, bool):
        return float(k)
    return k


def lua_type(v):
    if v is None:
        return "nil"
    if v is True or v is False:
        return "boolean"
    if isinstance(v, float):
        return "number"
    if isinstance(v, int):
        return "number"
    if isinstance(v, bytes):
        return "string"
    if isinstance(v, LuaTable):
        return "table"
    if isinstance(v, (LuaFunction, Builtin)):
        return "function"
    if isinstance(v, Buffer):
        return "buffer"
    return "userdata"


def fmt_num(v):
    if v != v:
        return "nan"
    if v == math.inf:
        return "inf"
    if v == -math.inf:
        return "-inf"
    if float(v).is_integer() and abs(v) < 1e15:
        return str(int(v))
    r = "%.14g" % v
    return r


def tostr(v):
    if v is None:
        return b"nil"
    if v is True:
        return b"true"
    if v is False:
        return b"false"
    if isinstance(v, (float, int)):
        return fmt_num(v).encode()
    if isinstance(v, bytes):
        return v
    return repr(v).encode()


def tonum(v):
    if isinstance(v, (float, int)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, bytes):
        try:
            s = v.decode().strip()
            if "_" in s:
                return None
            if s.lower().startswith(("0x", "-0x")):
                return float(int(s, 16))
            return float(s)
        except Exception:
            return None
    return None


def truthy(v):
    return v is not None and v is not False


def u32(x):
    x = tonum(x)
    if x is None:
        raise LuaError(b"bit32 arg not number")
    if x != x or x in (math.inf, -math.inf):
        return 0
    return int(math.fmod(math.trunc(x), 2 ** 32)) % (2 ** 32) if abs(x) < 2 ** 63 else int(x) % 2 ** 32


class Interp:
    def __init__(self):
        self.G = LuaTable(name="_G")
        self.step_count = 0
        self.install_stdlib()

    # ---------------- stdlib -----------------
    def reg(self, tbl, name, fn):
        full = name if tbl is self.G or tbl.name in (None, "_G") else tbl.name + "." + name
        tbl.h[name.encode()] = Builtin(fn, full)

    def install_stdlib(self):
        G = self.G
        I = self

        def b_print(args):
            print("[lua print]", *[tostr(a).decode("utf-8", "replace") for a in args])
            return []
        self.reg(G, "print", b_print)
        self.reg(G, "type", lambda a: [lua_type(a[0] if a else None).encode()])
        self.reg(G, "typeof", lambda a: [I.typeof(a[0] if a else None).encode()])
        self.reg(G, "tostring", lambda a: [tostr(a[0] if a else None)])

        def b_tonumber(a):
            v = a[0] if a else None
            base = a[1] if len(a) > 1 else None
            if base is not None and isinstance(v, bytes):
                try:
                    return [float(int(v.decode().strip(), int(base)))]
                except Exception:
                    return [None]
            return [tonum(v)]
        self.reg(G, "tonumber", b_tonumber)
        self.reg(G, "rawget", lambda a: [a[0].h.get(key_norm(a[1]))])

        def b_rawset(a):
            I.rawset(a[0], a[1], a[2] if len(a) > 2 else None)
            return [a[0]]
        self.reg(G, "rawset", b_rawset)
        self.reg(G, "rawequal", lambda a: [I.raweq(a[0], a[1])])
        self.reg(G, "rawlen", lambda a: [float(a[0].length() if isinstance(a[0], LuaTable) else len(a[0]))])

        def b_setmt(a):
            a[0].meta = a[1] if len(a) > 1 else None
            return [a[0]]
        self.reg(G, "setmetatable", b_setmt)
        self.reg(G, "getmetatable", lambda a: [a[0].meta if isinstance(a[0], LuaTable) else (I.string_meta if isinstance(a[0], bytes) else None)])

        def b_next(a):
            t = a[0]
            k = a[1] if len(a) > 1 else None
            return I.lua_next(t, k)
        self.reg(G, "next", b_next)
        nextfn = G.h[b"next"]
        self.reg(G, "pairs", lambda a: [nextfn, a[0], None])

        def ipairs_iter(a):
            t, i = a[0], a[1] + 1
            v = I.index(t, i)
            if v is None:
                return [None]
            return [i, v]
        ip = Builtin(ipairs_iter, "ipairs_iter")
        self.reg(G, "ipairs", lambda a: [ip, a[0], 0.0])

        def b_select(a):
            n = a[0]
            if n == b"#":
                return [float(len(a) - 1)]
            n = int(tonum(n))
            if n < 0:
                return a[n:]
            return a[n:]
        self.reg(G, "select", b_select)

        def b_error(a):
            raise LuaError(a[0] if a else None)
        self.reg(G, "error", b_error)

        def b_assert(a):
            if not a or not truthy(a[0]):
                raise LuaError(a[1] if len(a) > 1 else b"assertion failed!")
            return a
        self.reg(G, "assert", b_assert)

        def b_pcall(a):
            try:
                r = I.call(a[0], a[1:])
                return [True] + r
            except LuaError as e:
                return [False, e.value]
        self.reg(G, "pcall", b_pcall)

        def b_unpack(a):
            t = a[0]
            i = int(a[1]) if len(a) > 1 and a[1] is not None else 1
            j = int(a[2]) if len(a) > 2 and a[2] is not None else t.length()
            return [t.h.get(float(k)) for k in range(i, j + 1)]
        self.reg(G, "unpack", b_unpack)
        self.reg(G, "__step", lambda a: [])

        # math
        M = LuaTable(name="math")
        G.h[b"math"] = M
        def m_floor(a):
            x = a[0]
            return [x if (x != x or x in (math.inf, -math.inf)) else float(math.floor(x))]

        def m_ceil(a):
            x = a[0]
            return [x if (x != x or x in (math.inf, -math.inf)) else float(math.ceil(x))]

        def m_max(a):
            r = a[0]
            for x in a[1:]:
                if x > r:
                    r = x
            return [r]

        def m_min(a):
            r = a[0]
            for x in a[1:]:
                if x < r:
                    r = x
            return [r]

        def m_modf(a):
            x = a[0]
            if x in (math.inf, -math.inf):
                return [x, 0.0]
            if x != x:
                return [x, x]
            ip = float(math.trunc(x))
            return [ip, x - ip]

        def m_fmod(a):
            x, y = a[0], a[1]
            if y == 0 or x != x or y != y or x in (math.inf, -math.inf):
                return [math.nan]
            if y in (math.inf, -math.inf):
                return [x]
            return [math.fmod(x, y)]

        def m_sqrt(a):
            x = a[0]
            return [math.sqrt(x) if x >= 0 else (x if x != x else math.nan)]
        self.reg(M, "floor", m_floor)
        self.reg(M, "ceil", m_ceil)
        self.reg(M, "abs", lambda a: [abs(a[0])])
        self.reg(M, "max", m_max)
        self.reg(M, "min", m_min)
        self.reg(M, "sqrt", m_sqrt)
        self.reg(M, "modf", m_modf)
        self.reg(M, "fmod", m_fmod)
        self.reg(M, "random", lambda a: [0.5])
        self.reg(M, "ldexp", lambda a: [math.ldexp(a[0], int(a[1]))])
        self.reg(M, "frexp", lambda a: list(map(float, math.frexp(a[0]))))
        M.h[b"pi"] = math.pi
        M.h[b"huge"] = math.inf

        # bit32
        B = LuaTable(name="bit32")
        G.h[b"bit32"] = B

        def band(a):
            r = 0xFFFFFFFF
            for x in a:
                r &= u32(x)
            return [float(r)]

        def bor(a):
            r = 0
            for x in a:
                r |= u32(x)
            return [float(r)]

        def bxor(a):
            r = 0
            for x in a:
                r ^= u32(x)
            return [float(r)]

        def cint(v):
            # luaL_checkinteger in Luau: (int)double, truncation toward zero
            x = tonum(v)
            if x is None:
                raise LuaError(b"bit32 arg not number")
            if x != x or x in (math.inf, -math.inf) or abs(x) >= 2 ** 31:
                return -2 ** 31
            return int(math.trunc(x))

        def b_shift(r, i):
            r &= 0xFFFFFFFF
            if i < 0:
                i = -i
                r = 0 if i >= 32 else r >> i
            else:
                r = 0 if i >= 32 else (r << i) & 0xFFFFFFFF
            return [float(r)]

        def lshift(a):
            return b_shift(u32(a[0]), cint(a[1]))

        def rshift(a):
            return b_shift(u32(a[0]), -cint(a[1]))

        def arshift(a):
            r, i = u32(a[0]), cint(a[1])
            if i < 0 or not (r & 0x80000000):
                return b_shift(r, -i)
            if i >= 32:
                return [float(0xFFFFFFFF)]
            return [float(((r >> i) | (~(0xFFFFFFFF >> i))) & 0xFFFFFFFF)]

        def b_rot(r, i):
            i &= 31
            r &= 0xFFFFFFFF
            if i:
                r = ((r << i) | (r >> (32 - i))) & 0xFFFFFFFF
            return [float(r)]

        def lrotate(a):
            return b_rot(u32(a[0]), cint(a[1]))

        def rrotate(a):
            return b_rot(u32(a[0]), -cint(a[1]))

        def countlz(a):
            x = u32(a[0])
            return [float(32 - x.bit_length())]

        def countrz(a):
            x = u32(a[0])
            if x == 0:
                return [32.0]
            return [float((x & -x).bit_length() - 1)]

        def fieldargs(a, k):
            f = cint(a[k])
            w = cint(a[k + 1]) if len(a) > k + 1 and a[k + 1] is not None else 1
            if f < 0:
                raise LuaError(b"field cannot be negative")
            if w <= 0:
                raise LuaError(b"width must be positive")
            if f + w > 32:
                raise LuaError(b"trying to access non-existent bits")
            return f, w

        def extract(a):
            x = u32(a[0])
            f, w = fieldargs(a, 1)
            return [float((x >> f) & ((1 << w) - 1))]

        def replace(a):
            x, v = u32(a[0]), u32(a[1])
            f, w = fieldargs(a, 2)
            m = (1 << w) - 1
            v &= m
            return [float(((x & ~(m << f)) | (v << f)) & 0xFFFFFFFF)]

        for n, fn in [("band", band), ("bor", bor), ("bxor", bxor), ("lshift", lshift), ("rshift", rshift),
                      ("arshift", arshift), ("lrotate", lrotate), ("rrotate", rrotate), ("countlz", countlz),
                      ("countrz", countrz), ("extract", extract), ("replace", replace),
                      ("bnot", lambda a: [float(~u32(a[0]) & 0xFFFFFFFF)]),
                      ("btest", lambda a: [band(a)[0] != 0])]:
            self.reg(B, n, fn)

        # string
        S = LuaTable(name="string")
        G.h[b"string"] = S
        self.reg(S, "sub", lambda a: [I.str_sub(a)])
        self.reg(S, "len", lambda a: [float(len(a[0]))])
        self.reg(S, "byte", lambda a: I.str_byte(a))
        self.reg(S, "char", lambda a: [bytes(int(x) for x in a)])
        self.reg(S, "rep", lambda a: [a[0] * int(a[1])])
        self.reg(S, "lower", lambda a: [a[0].lower()])
        self.reg(S, "upper", lambda a: [a[0].upper()])
        self.reg(S, "reverse", lambda a: [a[0][::-1]])
        self.reg(S, "format", lambda a: [I.str_format(a)])
        self.reg(S, "unpack", lambda a: I.str_unpack(a))
        self.reg(S, "pack", lambda a: [I.str_pack(a)])
        def packsize(a):
            if any(k in ("s", "z") for k, n, e in I.parse_fmt(a[0])):
                raise LuaError(b"variable-length format")
            return [float(I.conv_fmt(a[0]))]
        self.reg(S, "packsize", packsize)
        self.reg(S, "gsub", lambda a: I.not_impl("string.gsub"))
        self.reg(S, "find", lambda a: I.not_impl("string.find"))
        self.reg(S, "match", lambda a: I.not_impl("string.match"))
        self.reg(S, "gmatch", lambda a: I.not_impl("string.gmatch"))
        self.string_meta = LuaTable(name="stringmeta")
        self.string_meta.h[b"__index"] = S

        # table
        T = LuaTable(name="table")
        G.h[b"table"] = T

        def t_insert(a):
            t = a[0]
            if len(a) == 2:
                t.h[float(t.length() + 1)] = a[1]
            else:
                pos = int(a[1])
                n = t.length()
                for i in range(n, pos - 1, -1):
                    t.h[float(i + 1)] = t.h.get(float(i))
                t.h[float(pos)] = a[2]
            return []
        self.reg(T, "insert", t_insert)

        def t_remove(a):
            t = a[0]
            n = t.length()
            pos = int(a[1]) if len(a) > 1 else n
            if n == 0:
                return [None]
            v = t.h.get(float(pos))
            for i in range(pos, n):
                t.h[float(i)] = t.h.get(float(i + 1))
            t.h.pop(float(n), None)
            return [v]
        self.reg(T, "remove", t_remove)
        self.reg(T, "create", lambda a: [LuaTable({float(i): a[1] for i in range(1, int(a[0]) + 1)} if len(a) > 1 and a[1] is not None else {})])
        self.reg(T, "unpack", b_unpack)

        def t_concat(a):
            t = a[0]
            sep = a[1] if len(a) > 1 and a[1] is not None else b""
            i = int(a[2]) if len(a) > 2 else 1
            j = int(a[3]) if len(a) > 3 else t.length()
            return [sep.join(tostr(t.h[float(k)]) for k in range(i, j + 1))]
        self.reg(T, "concat", t_concat)

        def t_move(a):
            src, f, e, t = a[0], int(a[1]), int(a[2]), int(a[3])
            dst = a[4] if len(a) > 4 and a[4] is not None else src
            if e >= f:
                vals = [src.h.get(float(k)) for k in range(f, e + 1)]
                for off, v in enumerate(vals):
                    I.rawset(dst, float(t + off), v)
            return [dst]
        self.reg(T, "move", t_move)
        self.reg(T, "pack", lambda a: [LuaTable(dict([(float(i + 1), v) for i, v in enumerate(a)] + [(b"n", float(len(a)))]))])
        self.reg(T, "clone", lambda a: [LuaTable(dict(a[0].h))])
        self.reg(T, "sort", lambda a: I.not_impl("table.sort"))
        self.reg(T, "find", lambda a: I.not_impl("table.find"))
        self.reg(T, "clear", lambda a: (a[0].h.clear(), [])[1])
        self.reg(T, "freeze", lambda a: [a[0]])
        self.reg(T, "isfrozen", lambda a: [False])

        # coroutine
        C = LuaTable(name="coroutine")
        G.h[b"coroutine"] = C
        for n in ("wrap", "yield", "create", "resume", "status", "isyieldable", "running", "close"):
            self.reg(C, n, (lambda nn: (lambda a: I.not_impl("coroutine." + nn)))(n))

        BF = LuaTable(name="buffer")
        G.h[b"buffer"] = BF
        self.reg(BF, "create", lambda a: [Buffer(int(a[0]))])

        def fromstring(a):
            bb = Buffer(0)
            bb.b = bytearray(a[0])
            return [bb]
        self.reg(BF, "fromstring", fromstring)
        self.reg(BF, "tostring", lambda a: [bytes(a[0].b)])
        self.reg(BF, "len", lambda a: [float(len(a[0].b))])
        for nm, fm in [("i8", "<b"), ("u8", "<B"), ("i16", "<h"), ("u16", "<H"), ("i32", "<i"), ("u32", "<I"),
                       ("f32", "<f"), ("f64", "<d")]:
            def mk(fm):
                def rd(a):
                    return [float(struct.unpack_from(fm, a[0].b, int(a[1]))[0])]

                def wr(a):
                    v = a[2]
                    if fm[1] in "bBhHiI":
                        v = int(v)
                        bits = struct.calcsize(fm) * 8
                        v &= (1 << bits) - 1
                        if fm[1].islower() and v >= 1 << (bits - 1):
                            v -= 1 << bits
                    struct.pack_into(fm, a[0].b, int(a[1]), v)
                    return []
                return rd, wr
            rd, wr = mk(fm)
            self.reg(BF, "read" + nm, rd)
            self.reg(BF, "write" + nm, wr)
        self.reg(BF, "readstring", lambda a: [bytes(a[0].b[int(a[1]):int(a[1]) + int(a[2])])])

        def writestring(a):
            data = a[2]
            n = int(a[3]) if len(a) > 3 and a[3] is not None else len(data)
            off = int(a[1])
            a[0].b[off:off + n] = data[:n]
            return []
        self.reg(BF, "writestring", writestring)

        def bcopy(a):
            tgt, toff, src = a[0], int(a[1]), a[2]
            soff = int(a[3]) if len(a) > 3 and a[3] is not None else 0
            n = int(a[4]) if len(a) > 4 and a[4] is not None else len(src.b) - soff
            tgt.b[toff:toff + n] = src.b[soff:soff + n]
            return []
        self.reg(BF, "copy", bcopy)

        def bfill(a):
            off, val = int(a[1]), int(a[2])
            n = int(a[3]) if len(a) > 3 and a[3] is not None else len(a[0].b) - off
            a[0].b[off:off + n] = bytes([val & 255]) * n
            return []
        self.reg(BF, "fill", bfill)

        O = LuaTable(name="os")
        G.h[b"os"] = O
        self.reg(O, "clock", lambda a: [1.0])
        self.reg(O, "time", lambda a: [1700000000.0])

    def not_impl(self, name):
        raise LuaError(("not implemented: " + name).encode())

    def typeof(self, v):
        return lua_type(v)

    def conv_fmt(self, f):
        # only used for packsize of simple formats
        size = 0
        for kind, n, endian in self.parse_fmt(f):
            size += n if kind not in ("s", "z") else 0
        return size

    def parse_fmt(self, f):
        """Luau string.pack format parser (sizes verified against the luau CLI):
        j/J = 4 (int), T = 4 (size_t), l/L = 8, s defaults to a 4-byte length prefix.
        Digits are only accepted after i, I, s, c and !. X consumes the next option and
        adds no padding because the default maximum alignment is 1."""
        f = f.decode("latin-1") if isinstance(f, bytes) else f
        out = []
        endian = "<"
        i = 0
        n_f = len(f)

        def readnum(i):
            j = i
            while j < n_f and f[j].isdigit():
                j += 1
            return (int(f[i:j]) if j > i else None), j
        while i < n_f:
            c = f[i]
            i += 1
            if c == "<":
                endian = "<"
            elif c == ">":
                endian = ">"
            elif c == "=":
                endian = "<"
            elif c == " ":
                pass
            elif c == "!":
                _, i = readnum(i)
            elif c in "bB":
                out.append((c, 1, endian))
            elif c in "hH":
                out.append((c, 2, endian))
            elif c in "iI":
                num, i = readnum(i)
                if num is not None and not (1 <= num <= 16):
                    raise LuaError(b"integral size out of limits [1,16]")
                out.append((c, num or 4, endian))
            elif c in "jJ":
                out.append(("i" if c == "j" else "I", 4, endian))
            elif c == "T":
                out.append(("T", 4, endian))
            elif c in "lL":
                out.append((c, 8, endian))
            elif c == "f":
                out.append(("f", 4, endian))
            elif c in "dn":
                out.append(("d", 8, endian))
            elif c == "s":
                num, i = readnum(i)
                out.append(("s", num or 4, endian))
            elif c == "z":
                out.append(("z", 0, endian))
            elif c == "x":
                out.append(("x", 1, endian))
            elif c == "c":
                num, i = readnum(i)
                if num is None:
                    raise LuaError(b"missing size for format option 'c'")
                out.append(("c", num, endian))
            elif c == "X":
                if i >= n_f or f[i] not in "bBhHiIjJTlLfdnx":
                    raise LuaError(b"invalid next option for option 'X'")
                nc = f[i]
                i += 1
                if nc in "iI":
                    _, i = readnum(i)
                # default maxalign is 1: no padding
            else:
                raise LuaError(b"invalid format option")
        return out

    def str_unpack(self, a):
        s = a[1]
        pos = int(a[2]) - 1 if len(a) > 2 and a[2] is not None else 0
        vals = []
        if pos < 0 or pos > len(s):
            raise LuaError(b"initial position out of string")
        for kind, n, endian in self.parse_fmt(a[0]):
            bo = "little" if endian == "<" else "big"
            if kind not in ("z", "s") and pos + n > len(s):
                raise LuaError(b"data string too short")
            if kind in "bhilj" or kind in ("b", "h", "i", "l", "j"):
                if kind in ("b", "h", "i", "l", "j"):
                    pass
            if kind in ("b", "h", "i", "l", "j"):
                vals.append(float(int.from_bytes(s[pos:pos + n], bo, signed=True)))
                pos += n
            elif kind in ("B", "H", "I", "L", "J", "T"):
                vals.append(float(int.from_bytes(s[pos:pos + n], bo, signed=False)))
                pos += n
            elif kind == "f":
                vals.append(float(struct.unpack((endian) + "f", s[pos:pos + 4])[0]))
                pos += 4
            elif kind == "d":
                vals.append(float(struct.unpack((endian) + "d", s[pos:pos + 8])[0]))
                pos += 8
            elif kind == "s":
                if pos + n > len(s):
                    raise LuaError(b"data string too short")
                ln = int.from_bytes(s[pos:pos + n], bo, signed=False)
                pos += n
                if pos + ln > len(s):
                    raise LuaError(b"data string too short")
                vals.append(bytes(s[pos:pos + ln]))
                pos += ln
            elif kind == "z":
                if b"\0" not in s[pos:]:
                    raise LuaError(b"unfinished string for format 'z'")
                e = s.index(b"\0", pos)
                vals.append(bytes(s[pos:e]))
                pos = e + 1
            elif kind == "c":
                vals.append(bytes(s[pos:pos + n]))
                pos += n
            elif kind == "x":
                pos += 1
        return vals + [float(pos + 1)]

    def str_pack(self, a):
        out = bytearray()
        vi = 1
        for kind, n, endian in self.parse_fmt(a[0]):
            bo = "little" if endian == "<" else "big"
            if kind in ("b", "h", "i", "l", "j"):
                v = int(a[vi])
                if n < 8:
                    lim = 1 << (8 * n - 1)
                    if not (-lim <= v < lim):
                        raise LuaError(b"integer overflow")
                out += (v % (1 << (8 * n))).to_bytes(n, bo, signed=False)
                vi += 1
            elif kind in ("B", "H", "I", "L", "J", "T"):
                v = int(a[vi])
                if n < 8 and not (0 <= v < (1 << (8 * n))):
                    raise LuaError(b"unsigned overflow")
                out += (v % (1 << (8 * n))).to_bytes(n, bo, signed=False)
                vi += 1
            elif kind == "f":
                out += struct.pack(endian + "f", a[vi])
                vi += 1
            elif kind == "d":
                out += struct.pack(endian + "d", a[vi])
                vi += 1
            elif kind == "s":
                v = a[vi]
                vi += 1
                out += len(v).to_bytes(n, bo) + v
            elif kind == "z":
                out += a[vi] + b"\0"
                vi += 1
            elif kind == "c":
                out += a[vi][:n].ljust(n, b"\0")
                vi += 1
            elif kind == "x":
                out += b"\0"
        return bytes(out)

    def str_sub(self, a):
        s = a[0]
        if not isinstance(s, bytes):
            s = tostr(s)
        n = len(s)
        i = int(a[1]) if len(a) > 1 and a[1] is not None else 1
        j = int(a[2]) if len(a) > 2 and a[2] is not None else -1
        if i < 0:
            i = max(n + i + 1, 1)
        elif i == 0:
            i = 1
        if j < 0:
            j = n + j + 1
        elif j > n:
            j = n
        if i > j:
            return b""
        return s[i - 1:j]

    def str_byte(self, a):
        s = a[0]
        n = len(s)
        i = int(a[1]) if len(a) > 1 and a[1] is not None else 1
        j = int(a[2]) if len(a) > 2 and a[2] is not None else i
        if i < 0:
            i = n + i + 1
        if j < 0:
            j = n + j + 1
        i = max(i, 1)
        j = min(j, n)
        return [float(c) for c in s[i - 1:j]]

    def str_format(self, a):
        fmt = a[0].decode("latin-1")
        out = []
        i = 0
        ai = 1
        while i < len(fmt):
            c = fmt[i]
            if c != "%":
                out.append(c)
                i += 1
                continue
            j = i + 1
            while fmt[j] in "-+ #0123456789.":
                j += 1
            spec = fmt[i:j + 1]
            conv = fmt[j]
            i = j + 1
            if conv == "%":
                out.append("%")
                continue
            v = a[ai]
            ai += 1
            if conv in "dixXc":
                out.append(spec % int(v) if conv != "c" else chr(int(v)))
            elif conv in "feEgG":
                out.append(spec % v)
            elif conv == "s":
                out.append((spec % tostr(v).decode("latin-1")))
            elif conv == "q":
                out.append(repr(tostr(v)))
            else:
                out.append(spec)
        return "".join(out).encode("latin-1")

    # ---------------- core ops -----------------
    def raweq(self, a, b):
        if isinstance(a, float) and isinstance(b, float):
            return a == b
        if a is b:
            return True
        if isinstance(a, bool) or isinstance(b, bool):
            return False
        if isinstance(a, (float, int)) and isinstance(b, (float, int)):
            return a == b
        if isinstance(a, bytes) and isinstance(b, bytes):
            return a == b
        return False

    def rawset(self, t, k, v):
        k = key_norm(k)
        if k is None:
            raise LuaError(b"table index is nil")
        if v is None:
            if k in t.h:
                # Lua keeps a removed key usable as a `next` cursor during traversal: remember its successor
                succ = None
                found = False
                for kk in t.h:
                    if found:
                        succ = kk
                        break
                    if kk == k and type(kk) == type(k):
                        found = True
                if t.dead is None:
                    t.dead = {}
                t.dead[(type(k), k)] = (succ,)
                del t.h[k]
        else:
            t.h[k] = v

    def lua_next(self, t, k):
        h = t.h
        if k is None:
            it = iter(h.items())
            for kk, vv in it:
                return [kk, vv]
            return [None]
        if k not in h and t.dead is not None:
            # cursor key was removed during traversal: continue from its successor
            seen = set()
            cur = k
            while True:
                ent = t.dead.get((type(cur), cur))
                if ent is None:
                    break
                nxt = ent[0]
                if nxt is None:
                    return [None]
                if nxt in h:
                    return [nxt, h[nxt]]
                if (type(nxt), nxt) in seen:
                    return [None]
                seen.add((type(nxt), nxt))
                cur = nxt
        # slow path: find k then next
        found = False
        for kk, vv in list(h.items()):
            if found:
                return [kk, vv]
            if kk == k and type(kk) == type(k):
                found = True
        return [None]

    def getmeta(self, v):
        if isinstance(v, LuaTable):
            return v.meta
        if isinstance(v, bytes):
            return self.string_meta
        return None

    def index(self, obj, key):
        if isinstance(obj, LuaTable):
            k = key_norm(key)
            v = obj.h.get(k)
            if v is not None:
                return v
            mt = obj.meta
            if mt is None:
                return None
            h = mt.h.get(b"__index")
            if h is None:
                return None
            if isinstance(h, LuaTable):
                return self.index(h, key)
            r = self.call(h, [obj, key])
            return r[0] if r else None
        if isinstance(obj, bytes):
            return self.index(self.string_meta.h[b"__index"], key)
        return self.index_other(obj, key)

    def index_other(self, obj, key):
        raise LuaError(b"attempt to index " + lua_type(obj).encode() + b" with " + tostr(key))

    def setindex(self, obj, key, val):
        if isinstance(obj, LuaTable):
            k = key_norm(key)
            if obj.meta is not None and k not in obj.h:
                h = obj.meta.h.get(b"__newindex")
                if h is not None:
                    if isinstance(h, LuaTable):
                        return self.setindex(h, key, val)
                    self.call(h, [obj, key, val])
                    return
            self.rawset(obj, k, val)
            return
        self.setindex_other(obj, key, val)

    def setindex_other(self, obj, key, val):
        raise LuaError(b"attempt to index " + lua_type(obj).encode())

    def call(self, f, args):
        if isinstance(f, LuaFunction):
            return self.call_lua(f, args)
        if isinstance(f, Builtin):
            r = f.fn(args)
            return r if r is not None else []
        if isinstance(f, LuaTable) and f.meta is not None and b"__call" in f.meta.h:
            return self.call(f.meta.h[b"__call"], [f] + list(args))
        return self.call_other(f, args)

    def call_other(self, f, args):
        raise LuaError(b"attempt to call a " + lua_type(f).encode() + b" value")

    def call_lua(self, f, args):
        node = f.node
        params, vararg, body = node.a
        sc = Scope(f.scope)
        v = sc.vars
        n = len(args)
        for i, p in enumerate(params):
            v[p] = args[i] if i < n else None
        sc.varargs = list(args[len(params):]) if vararg else []
        r = self.exec_block(body, sc)
        if r is None or r is BREAK or r is CONTINUE:
            return []
        return r[1]

    def arith(self, op, a, b):
        x, y = a, b
        if not (isinstance(x, float) and isinstance(y, float)):
            xn, yn = tonum(x), tonum(y)
            if xn is None or yn is None:
                return self.arith_meta(op, a, b)
            x, y = xn, yn
        try:
            if op == "+":
                return x + y
            if op == "-":
                return x - y
            if op == "*":
                return x * y
            if op == "/":
                if y == 0:
                    if x == 0 or x != x:
                        return math.nan
                    return math.copysign(math.inf, x) * (1 if math.copysign(1, y) > 0 else -1)
                return x / y
            if op == "%":
                if y == 0:
                    return math.nan
                if math.isinf(y):
                    if x != x or math.isinf(x):
                        return math.nan
                    if (x >= 0) == (y > 0):
                        return x
                    return y
                r = math.fmod(x, y)
                if r != 0 and (r < 0) != (y < 0):
                    r += y
                return r
            if op == "^":
                try:
                    return math.pow(x, y)
                except (OverflowError, ValueError):
                    return math.inf if x > 0 else math.nan
            if op == "//":
                if y == 0:
                    return self.arith("/", x, y)
                return float(math.floor(x / y))
        except OverflowError:
            return math.inf
        raise LuaError(b"bad arith " + op.encode())

    def arith_meta(self, op, a, b):
        ev = {"+": b"__add", "-": b"__sub", "*": b"__mul", "/": b"__div", "%": b"__mod", "^": b"__pow", "//": b"__idiv"}[op]
        for o in (a, b):
            mt = self.getmeta(o)
            if mt is not None and ev in mt.h:
                r = self.call(mt.h[ev], [a, b])
                return r[0] if r else None
        raise LuaError(b"attempt to perform arithmetic (" + op.encode() + b") on " + lua_type(a).encode() + b" and " + lua_type(b).encode())

    def concat(self, a, b):
        if isinstance(a, (bytes, float, int)) and isinstance(b, (bytes, float, int)) and not isinstance(a, bool) and not isinstance(b, bool):
            return tostr(a) + tostr(b)
        for o in (a, b):
            mt = self.getmeta(o)
            if mt is not None and b"__concat" in mt.h:
                r = self.call(mt.h[b"__concat"], [a, b])
                return r[0] if r else None
        raise LuaError(b"attempt to concatenate " + lua_type(a).encode() + b" with " + lua_type(b).encode())

    def eq(self, a, b):
        if self.raweq(a, b):
            return True
        if isinstance(a, LuaTable) and isinstance(b, LuaTable):
            for o in (a, b):
                mt = o.meta
                if mt is not None and b"__eq" in mt.h:
                    r = self.call(mt.h[b"__eq"], [a, b])
                    return truthy(r[0] if r else None)
        return False

    def lt(self, a, b):
        if isinstance(a, float) and isinstance(b, float):
            return a < b
        if isinstance(a, bytes) and isinstance(b, bytes):
            return a < b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
            return a < b
        return self.cmp_meta(b"__lt", a, b)

    def le(self, a, b):
        if isinstance(a, float) and isinstance(b, float):
            return a <= b
        if isinstance(a, bytes) and isinstance(b, bytes):
            return a <= b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
            return a <= b
        return self.cmp_meta(b"__le", a, b)

    def cmp_meta(self, ev, a, b):
        for o in (a, b):
            mt = self.getmeta(o)
            if mt is not None and ev in mt.h:
                r = self.call(mt.h[ev], [a, b])
                return truthy(r[0] if r else None)
        raise LuaError(b"attempt to compare " + lua_type(a).encode() + b" with " + lua_type(b).encode())

    def length(self, v):
        if isinstance(v, bytes):
            return float(len(v))
        if isinstance(v, LuaTable):
            mt = v.meta
            if mt is not None and b"__len" in mt.h:
                r = self.call(mt.h[b"__len"], [v])
                return r[0] if r else None
            return float(v.length())
        return self.length_other(v)

    def length_other(self, v):
        raise LuaError(b"attempt to get length of " + lua_type(v).encode())

    def neg(self, v):
        n = tonum(v)
        if n is None:
            mt = self.getmeta(v)
            if mt is not None and b"__unm" in mt.h:
                return self.call(mt.h[b"__unm"], [v])[0]
            raise LuaError(b"attempt to negate")
        return -n

    def test(self, v):
        """truthiness test; overridable for symbolic values"""
        return v is not None and v is not False

    # ---------------- evaluation -----------------
    def lookup_var(self, name, scope):
        s = scope
        while s is not None:
            v = s.vars
            if name in v:
                return v[name]
            s = s.parent
        return self.index(self.G, name.encode())

    def set_var(self, name, val, scope):
        s = scope
        while s is not None:
            v = s.vars
            if name in v:
                v[name] = val
                return
            s = s.parent
        self.setindex(self.G, name.encode(), val)

    def eval(self, e, sc):
        k = e.k
        if k == "Const":
            return e.a[0]
        if k == "Name":
            return self.lookup_var(e.a[0], sc)
        if k == "Index":
            o = self.eval(e.a[0], sc)
            key = self.eval(e.a[1], sc)
            return self.index(o, key)
        if k == "Binop":
            op = e.a[0]
            a = self.eval(e.a[1], sc)
            b = self.eval(e.a[2], sc)
            return self.binop(op, a, b)
        if k == "And":
            a = self.eval(e.a[0], sc)
            if not self.test(a):
                return a
            return self.eval(e.a[1], sc)
        if k == "Or":
            a = self.eval(e.a[0], sc)
            if self.test(a):
                return a
            return self.eval(e.a[1], sc)
        if k == "Unop":
            op = e.a[0]
            v = self.eval(e.a[1], sc)
            if op == "not":
                return not self.test(v)
            if op == "-":
                return self.neg(v)
            if op == "#":
                return self.length(v)
        if k in ("Call", "Method", "Vararg"):
            r = self.eval_multi(e, sc)
            return r[0] if r else None
        if k == "Paren":
            return self.eval(e.a[0], sc)
        if k == "Function":
            return LuaFunction(e, sc)
        if k == "Table":
            return self.eval_table(e, sc)
        if k == "IfExpr":
            c = self.eval(e.a[0], sc)
            return self.eval(e.a[1], sc) if self.test(c) else self.eval(e.a[2], sc)
        if k == "IString":
            return self.eval_istring(e, sc)
        raise LuaError(("cannot eval " + k).encode())

    def eval_istring(self, e, sc):
        raise LuaError(b"interpolated strings unsupported")

    def binop(self, op, a, b):
        if op in ("+", "-", "*", "/", "%", "^", "//"):
            if isinstance(a, float) and isinstance(b, float) and op in ("+", "-", "*"):
                if op == "+":
                    return a + b
                if op == "-":
                    return a - b
                return a * b
            return self.arith(op, a, b)
        if op == "..":
            return self.concat(a, b)
        if op == "==":
            return self.eq(a, b)
        if op == "~=":
            return not self.eq(a, b)
        if op == "<":
            return self.lt(a, b)
        if op == "<=":
            return self.le(a, b)
        if op == ">":
            return self.lt(b, a)
        if op == ">=":
            return self.le(b, a)
        raise LuaError(("bad op " + op).encode())

    def eval_table(self, e, sc):
        t = LuaTable()
        items = e.a[0]
        n = 1
        last = len(items) - 1
        for idx, (kind, kexp, vexp) in enumerate(items):
            if kind == "kv":
                key = self.eval(kexp, sc)
                val = self.eval(vexp, sc)
                if val is not None:
                    self.rawset(t, key, val)
            else:
                if idx == last and vexp.k in ("Call", "Method", "Vararg"):
                    vals = self.eval_multi(vexp, sc)
                    for v in vals:
                        if v is not None:
                            t.h[float(n)] = v
                        n += 1
                else:
                    v = self.eval(vexp, sc)
                    if v is not None:
                        t.h[float(n)] = v
                    n += 1
        return t

    def eval_args(self, args, sc):
        if not args:
            return []
        out = [self.eval(a, sc) for a in args[:-1]]
        last = args[-1]
        if last.k in ("Call", "Method", "Vararg"):
            out.extend(self.eval_multi(last, sc))
        else:
            out.append(self.eval(last, sc))
        return out

    def eval_multi(self, e, sc):
        k = e.k
        if k == "Call":
            f = self.eval(e.a[0], sc)
            args = self.eval_args(e.a[1], sc)
            return self.call(f, args)
        if k == "Method":
            o = self.eval(e.a[0], sc)
            f = self.index(o, e.a[1].encode())
            args = self.eval_args(e.a[2], sc)
            return self.call(f, [o] + args)
        if k == "Vararg":
            return list(sc.varargs)
        return [self.eval(e, sc)]

    def assign(self, target, val, sc):
        if target.k == "Name":
            self.set_var(target.a[0], val, sc)
        elif target.k == "Index":
            o = self.eval(target.a[0], sc)
            key = self.eval(target.a[1], sc)
            self.setindex(o, key, val)
        else:
            raise LuaError(b"bad assignment target")

    def exec_block(self, stmts, sc):
        for s in stmts:
            try:
                r = self.exec_stmt(s, sc)
            except Exception as e:
                if not getattr(e, "line", None):
                    try:
                        e.line = s.line
                    except Exception:
                        pass
                raise
            if r is not None:
                return r
        return None

    def exec_stmt(self, s, sc):
        k = s.k
        if k == "Local":
            names, exprs = s.a
            if len(names) == 1 and len(exprs) == 1:
                sc.vars[names[0]] = self.eval(exprs[0], sc)
                return None
            vals = self.eval_args(exprs, sc)
            for i, n in enumerate(names):
                sc.vars[n] = vals[i] if i < len(vals) else None
            return None
        if k == "Assign":
            targets, exprs = s.a
            if len(targets) == 1 and len(exprs) == 1:
                t = targets[0]
                if t.k == "Index":
                    o = self.eval(t.a[0], sc)
                    key = self.eval(t.a[1], sc)
                    val = self.eval(exprs[0], sc)
                    self.setindex(o, key, val)
                else:
                    self.set_var(t.a[0], self.eval(exprs[0], sc), sc)
                return None
            # evaluate target prefixes first, then values
            pre = []
            for t in targets:
                if t.k == "Index":
                    pre.append((self.eval(t.a[0], sc), self.eval(t.a[1], sc)))
                else:
                    pre.append(None)
            vals = self.eval_args(exprs, sc)
            for i in range(len(targets) - 1, -1, -1):
                t = targets[i]
                v = vals[i] if i < len(vals) else None
                if pre[i] is None:
                    self.set_var(t.a[0], v, sc)
                else:
                    self.setindex(pre[i][0], pre[i][1], v)
            return None
        if k == "CompoundAssign":
            op, t, rhs = s.a
            if t.k == "Index":
                o = self.eval(t.a[0], sc)
                key = self.eval(t.a[1], sc)
                cur = self.index(o, key)
                self.setindex(o, key, self.binop(op, cur, self.eval(rhs, sc)))
            else:
                cur = self.lookup_var(t.a[0], sc)
                self.set_var(t.a[0], self.binop(op, cur, self.eval(rhs, sc)), sc)
            return None
        if k == "CallStat":
            self.eval_multi(s.a[0], sc)
            return None
        if k == "If":
            cond, body, orelse = s.a
            if self.test(self.eval(cond, sc)):
                return self.exec_block(body, Scope(sc))
            elif orelse is not None:
                return self.exec_block(orelse, Scope(sc))
            return None
        if k == "While":
            cond, body = s.a
            while self.test(self.eval(cond, sc)):
                r = self.exec_block(body, Scope(sc))
                if r is not None:
                    if r is BREAK:
                        break
                    if r is CONTINUE:
                        continue
                    return r
            return None
        if k == "Repeat":
            body, cond = s.a
            while True:
                inner = Scope(sc)
                r = self.exec_block(body, inner)
                if r is not None:
                    if r is BREAK:
                        break
                    if r is not CONTINUE:
                        return r
                if self.test(self.eval(cond, inner)):
                    break
            return None
        if k == "NumFor":
            var, e1, e2, e3, body = s.a
            a = tonum(self.eval(e1, sc))
            b = tonum(self.eval(e2, sc))
            st = tonum(self.eval(e3, sc)) if e3 is not None else 1.0
            i = a
            while (st > 0 and i <= b) or (st <= 0 and i >= b):
                inner = Scope(sc)
                inner.vars[var] = i
                r = self.exec_block(body, inner)
                if r is not None:
                    if r is BREAK:
                        break
                    if r is not CONTINUE:
                        return r
                i += st
            return None
        if k == "GenFor":
            names, exprs, body = s.a
            vals = self.eval_args(exprs, sc)
            f = vals[0] if vals else None
            st = vals[1] if len(vals) > 1 else None
            ctl = vals[2] if len(vals) > 2 else None
            if isinstance(f, LuaTable) and (f.meta is None or b"__call" not in f.meta.h):
                # generalized iteration
                st = f
                f = self.G.h[b"next"]
                ctl = None
            while True:
                rs = self.call(f, [st, ctl])
                if not rs or rs[0] is None:
                    break
                ctl = rs[0]
                inner = Scope(sc)
                for i, n in enumerate(names):
                    inner.vars[n] = rs[i] if i < len(rs) else None
                r = self.exec_block(body, inner)
                if r is not None:
                    if r is BREAK:
                        break
                    if r is not CONTINUE:
                        return r
            return None
        if k == "Do":
            return self.exec_block(s.a[0], Scope(sc))
        if k == "Return":
            return ("return", self.eval_args(s.a[0], sc))
        if k == "Break":
            return BREAK
        if k == "Continue":
            return CONTINUE
        if k == "LocalFunction":
            name, fnode = s.a
            sc.vars[name] = None
            sc.vars[name] = LuaFunction(fnode, sc, name)
            return None
        raise LuaError(("cannot exec " + k).encode())

    def run_chunk(self, ast, args=()):
        f = LuaFunction(Node("Function", [], True, ast), Scope(None))
        return self.call(f, list(args))
