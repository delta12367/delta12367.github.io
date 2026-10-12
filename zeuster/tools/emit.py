"""Lua source emission from structured statements + IR expressions."""
import math, re

KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil",
            "not", "or", "repeat", "return", "then", "true", "until", "while", "continue", "goto"}
IDENT = re.compile(rb"^[A-Za-z_][A-Za-z0-9_]*$")

BIN_PREC = {"or": (1, 1), "and": (2, 2), "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3),
            "==": (3, 3), "..": (5, 4), "+": (6, 6), "-": (6, 6), "*": (7, 7), "/": (7, 7), "//": (7, 7),
            "%": (7, 7), "^": (10, 9)}
UNARY_PREC = 8


def is_ident(b):
    return isinstance(b, bytes) and IDENT.match(b) is not None and b.decode() not in KEYWORDS


def lua_string(b):
    try:
        s = b.decode("utf-8")
        utf8_ok = True
    except UnicodeDecodeError:
        utf8_ok = False
    out = ['"']
    if utf8_ok:
        chars = s
        for i, ch in enumerate(chars):
            o = ord(ch)
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif o < 32 or o == 127:
                nxt = chars[i + 1] if i + 1 < len(chars) else ""
                if nxt.isdigit():
                    out.append("\\%03d" % o)
                else:
                    out.append("\\%d" % o)
            else:
                out.append(ch)
    else:
        for i, o in enumerate(b):
            ch = chr(o)
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif 32 <= o < 127:
                out.append(ch)
            else:
                nxt = b[i + 1:i + 2]
                if nxt and nxt.isdigit():
                    out.append("\\%03d" % o)
                else:
                    out.append("\\%d" % o)
    out.append('"')
    return "".join(out)


def lua_number(v):
    if v != v:
        return "(0 / 0)"
    if v == math.inf:
        return "math.huge"
    if v == -math.inf:
        return "-math.huge"
    if float(v).is_integer() and abs(v) < 2 ** 63:
        return str(int(v))
    r = repr(float(v))
    return r


def lua_const(v):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return lua_number(v)
    if isinstance(v, bytes):
        return lua_string(v)
    return "--[[?%r]]nil" % (v,)


class Emitter:
    """ctx object must provide: name(var) -> str, upval(i) -> str, param(i) -> str, nparams, closure(e, indent) -> str,
    native(e, indent) -> str, hostfn(e) -> str"""

    def __init__(self, ctx):
        self.ctx = ctx

    def expr(self, e, prec=0, indent=""):
        s, p = self.ex(e, indent)
        if p < prec:
            return "(" + s + ")"
        return s

    def ex(self, e, indent):
        k = e[0]
        c = self.ctx
        if k == "k":
            v = e[1]
            if isinstance(v, float) and v < 0:
                return lua_const(v), UNARY_PREC
            return lua_const(v), 100
        if k in ("r", "t", "v", "x", "w", "s"):
            return c.name(e), 100
        if k == "u":
            return c.upval(e[1]), 100
        if k == "spill":
            return c.name(("w", e[1])) + "." + c.name(("w", e[2])), 100
        if k == "p":
            return c.param(e[1]), 100
        if k == "g":
            key = e[1]
            if key[0] == "k" and is_ident(key[1]):
                return key[1].decode(), 100
            return "getfenv()[" + self.expr(key, 0, indent) + "]", 100
        if k == "idx":
            o = self.prefix(e[1], indent)
            key = e[2]
            if key[0] == "k" and is_ident(key[1]):
                return o + "." + key[1].decode(), 100
            return o + "[" + self.expr(key, 0, indent) + "]", 100
        if k == "bin":
            op = e[1]
            lp, rp = BIN_PREC[op]
            a = self.expr(e[2], lp if op not in ("..", "^") else lp + 1, indent)
            b = self.expr(e[3], rp + (1 if op not in ("..", "^") else 0), indent)
            return a + " " + op + " " + b, lp
        if k == "un":
            op = e[1]
            inner = self.expr(e[2], UNARY_PREC, indent)
            if op == "not":
                return "not " + inner, UNARY_PREC
            if op == "-" and inner.startswith("-"):
                return "- " + inner, UNARY_PREC
            return op + inner, UNARY_PREC
        if k in ("and", "or"):
            lp, rp = BIN_PREC[k]
            return self.expr(e[1], lp, indent) + " " + k + " " + self.expr(e[2], rp + 1, indent), lp
        if k in ("callx", "callmx"):
            return self.call(e[1], e[2], indent), 100
        if k in ("mcallx", "mcallmx"):
            obj = self.prefix(e[1], indent)
            return obj + ":" + e[2].decode() + "(" + self.args(list(e[3]), indent) + ")", 100
        if k == "newtable":
            return self.table(e, indent), 100
        if k == "closure":
            return c.closure(e, indent), 100
        if k == "native":
            return c.native(e, indent), 100
        if k == "builtin":
            return e[1], 100
        if k == "static":
            return "nil --[[vm:" + e[1] + "]]", 100
        if k == "hostfn":
            return c.hostfn(e), 100
        if k == "multi":
            src = e[1]
            if src[0] == "va":
                return self.vararg(src), 100
            return "--[[multi " + repr(src) + "]]", 100
        if k == "count":
            src = e[1]
            if src[0] == "va":
                return "select(\"#\", ...)", 100
            return "--[[count]]0", 100
        if k == "flagvar":
            return c.flag_name(e[1]), 100
        if k == "regfile":
            return "nil --[[registers]]", 100
        if k == "fgok":
            return "--[[forok]]true", 100
        if k == "fgv":
            return "--[[forvar%d]]nil" % e[2], 100
        if k == "sel":
            return "--[[sel %r]]nil" % (e[1:],), 100
        return "--[[?" + k + "]]nil", 100

    def vararg(self, src):
        s = src[1] if len(src) > 1 else 1
        n = self.ctx.nparams
        if s == n + 1:
            return "..."
        if s > n + 1:
            return "select(%d, ...)" % (s - n)
        return "..."

    def call(self, f, args, indent):
        # method call sugar
        if f[0] == "idx" and f[2][0] == "k" and is_ident(f[2][1]) and args and args[0] == f[1] and simple(f[1]) and f[1][0] != "k":
            obj = self.expr(f[1], 11, indent)
            return obj + ":" + f[2][1].decode() + "(" + self.args(args[1:], indent) + ")"
        fs = self.prefix(f, indent)
        return fs + "(" + self.args(args, indent) + ")"

    def prefix(self, e, indent):
        """an expression usable before `.x`, `[k]`, `:m()` or `(args)`"""
        s = self.expr(e, 0, indent)
        if e[0] in PREFIX_KINDS and not (e[0] == "g" and s.startswith("getfenv")):
            return s
        if e[0] == "g":
            return s
        if e[0] == "u" or e[0] == "p":
            return s if IDENT_S.match(s) or s.startswith("(") else "(" + s + ")"
        return "(" + s + ")"

    def args(self, args, indent):
        parts = []
        for i, a in enumerate(args):
            last = i == len(args) - 1
            if a[0] == "multi":
                parts.append(self.expr(a, 0, indent))
            elif a[0] in ("callmx", "mcallmx") and not last:
                parts.append("(" + self.expr(a, 0, indent) + ")")
            else:
                parts.append(self.expr(a, 0, indent))
        return ", ".join(parts)

    def table(self, e, indent):
        arr, hs = e[1], e[2]
        if not arr and not hs:
            return "{}"
        items = []
        for i, a in enumerate(arr):
            items.append(self.expr(a, 0, indent + "    "))
        for k, v in hs:
            if k[0] == "k" and is_ident(k[1]):
                items.append(k[1].decode() + " = " + self.expr(v, 0, indent + "    "))
            else:
                items.append("[" + self.expr(k, 0, indent + "    ") + "] = " + self.expr(v, 0, indent + "    "))
        one = "{" + ", ".join(items) + "}"
        if len(one) <= 100 and "\n" not in one:
            return one
        return "{\n" + "".join(indent + "    " + it + ",\n" for it in items) + indent + "}"


PREFIX_KINDS = {"r", "t", "v", "x", "w", "s", "spill", "idx", "callx", "callmx", "mcallx", "mcallmx", "flagvar"}
IDENT_S = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def simple(e):
    return e[0] in ("r", "t", "v", "x", "w", "u", "p", "g", "k")


def lhs_ok(e):
    return e[0] in ("r", "t", "v", "x", "w", "u", "g", "idx")
