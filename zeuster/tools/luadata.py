"""Parse the Backend.loadProgramData() section (constants + data tables) into Python objects."""
import re, pickle, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))


class Ref:
    __slots__ = ("i",)

    def __init__(self, i):
        self.i = i

    def __repr__(self):
        return f"data[{self.i}]"


def unescape(body, quote):
    out = bytearray()
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
            continue
        i += 1
        c = body[i]
        simple = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}
        if c in simple:
            out.append(simple[c])
            i += 1
        elif c == "x":
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
        elif c.isdigit():
            j = i
            while j < n and j < i + 3 and body[j].isdigit():
                j += 1
            out.append(int(body[i:j]))
            i = j
        elif c == "z":
            i += 1
            while i < n and body[i] in " \t\r\n":
                i += 1
        elif c == "u":
            # \u{XXXX}
            j = body.index("}", i)
            cp = int(body[i + 2:j], 16)
            out += chr(cp).encode("utf-8")
            i = j + 1
        else:
            raise ValueError("bad escape " + repr(c))
    return bytes(out)


TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<long>\[(?P<eq>=*)\[(?P<lbody>.*?)\](?P=eq)\])
  | (?P<str>'(?:[^'\\\n]|\\.|\\\n)*'|"(?:[^"\\\n]|\\.|\\\n)*")
  | (?P<num>0[xX][0-9a-fA-F_]+|0[bB][01_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|[-+*/%^#<>=(){}\[\];:,.])
""", re.S | re.X)


def tokenize(src):
    pos = 0
    toks = []
    n = len(src)
    while pos < n:
        m = TOKEN.match(src, pos)
        if not m:
            raise SyntaxError("bad token at %d: %r" % (pos, src[pos:pos + 40]))
        pos = m.end()
        k = m.lastgroup
        if k == "ws":
            continue
        if k == "long":
            body = m.group("lbody")
            if body.startswith("\r\n"):
                body = body[2:]
            elif body.startswith("\n"):
                body = body[1:]
            toks.append(("str", body.encode("utf-8")))
        elif k == "str":
            s = m.group("str")
            toks.append(("str", unescape(s[1:-1], s[0])))
        elif k == "num":
            t = m.group("num").replace("_", "")
            if t[:2] in ("0x", "0X"):
                v = int(t, 16)
            elif t[:2] in ("0b", "0B"):
                v = int(t[2:], 2)
            else:
                v = float(t)
                if v.is_integer() and abs(v) < 2 ** 53:
                    v = int(v)
            toks.append(("num", v))
        elif k == "name":
            toks.append(("name", m.group("name")))
        else:
            toks.append(("op", m.group("op")))
    return toks


class P:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tok = self.t[self.i]
        if kind and tok[0] != kind:
            raise SyntaxError(f"expected {kind} {val} got {tok} at {self.i}")
        if val is not None and tok[1] != val:
            raise SyntaxError(f"expected {val} got {tok} at {self.i}")
        self.i += 1
        return tok

    def value(self):
        k, v = self.peek()
        if k == "op" and v == "-":
            self.take()
            return -self.value()
        if k == "op" and v == "(":
            # (1 / 0) style or (0 / 0) or (-1/0)
            self.take()
            a = self.value()
            op = self.take("op")[1]
            b = self.value()
            self.take("op", ")")
            if op == "/":
                if b == 0:
                    if a == 0:
                        return float("nan")
                    return float("inf") if a > 0 else float("-inf")
                return a / b
            raise SyntaxError("unsupported expr")
        if k == "num":
            self.take()
            return v
        if k == "str":
            self.take()
            return v
        if k == "name":
            if v == "true":
                self.take()
                return True
            if v == "false":
                self.take()
                return False
            if v == "nil":
                self.take()
                return None
            if v == "data":
                self.take()
                self.take("op", "[")
                n = self.take("num")[1]
                self.take("op", "]")
                return Ref(n)
            if v == "math":
                self.take(); self.take("op", "."); w = self.take("name")[1]
                if w == "huge":
                    return float("inf")
                if w == "pi":
                    import math
                    return math.pi
                raise SyntaxError(w)
        raise SyntaxError(f"bad value {self.peek()} at {self.i}")

    def table(self):
        self.take("op", "{")
        out = {}
        while True:
            k, v = self.peek()
            if k == "op" and v == "}":
                self.take()
                return out
            self.take("op", "[")
            key = self.value()
            self.take("op", "]")
            self.take("op", "=")
            out[key] = self.value()
            k, v = self.peek()
            if k == "op" and v in (",", ";"):
                self.take()


def parse(path):
    src = open(path, encoding="utf-8").read()
    start = src.index("function Backend.loadProgramData()")
    end = src.index("return runApplication()", start)
    sec = src[start:end]
    # constants
    cstart = sec.index("local constants = ") + len("local constants = ")
    cend = sec.index("local data = table.create(")
    constants = P(tokenize(sec[cstart:cend])).table()
    count = int(re.search(r"table\.create\((\d+)\)", sec).group(1))
    data = {}
    for m in re.finditer(r"fill\(data\[(\d+)\], ", sec):
        pass
    # parse fills
    body = sec[cend:]
    fills = {}
    pos = 0
    pat = re.compile(r"fill\(data\[(\d+)\], ")
    idx = [(m.start(), m.end(), int(m.group(1))) for m in pat.finditer(body)]
    for k, (s, e, n) in enumerate(idx):
        if k == 0:
            continue  # first match is the definition `local function fill(target, values)`? guard
    fills = {}
    for s, e, n in idx:
        # find table text: from e to matching '})'
        # tokenize lazily: find end as next "\n    })" occurrence
        tend = body.index("\n    })", e)
        txt = body[e:tend + 6]
        fills[n] = P(tokenize(txt)).table()
    metas = [int(x) for x in re.findall(r"setmetatable\(data\[(\d+)\], constantMetatable\)", body)]
    return constants, count, fills, metas


if __name__ == "__main__":
    path = sys.argv[1]
    constants, count, fills, metas = parse(path)
    print("constants", len(constants), "count", count, "fills", len(fills), "metas", len(metas))
    with open(os.path.join(HERE, "progdata.pkl"), "wb") as f:
        pickle.dump((constants, count, fills, metas), f)
