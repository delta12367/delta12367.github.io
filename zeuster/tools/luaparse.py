"""A small Luau parser producing a simple AST (tuples wrapped in Node objects)."""
import re
from luadata import unescape


class Node:
    __slots__ = ("k", "a", "line")

    def __init__(self, k, *a, line=0):
        self.k = k
        self.a = list(a)
        self.line = line

    def __repr__(self):
        return f"{self.k}{tuple(self.a)!r}"


KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in",
            "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}

TOK = re.compile(r"""
    (?P<nl>\n)
  | (?P<ws>[ \t\r]+)
  | (?P<lcomment>--\[(?P<ceq>=*)\[.*?\](?P=ceq)\])
  | (?P<comment>--[^\n]*)
  | (?P<long>\[(?P<eq>=*)\[(?P<lbody>.*?)\](?P=eq)\])
  | (?P<str>'(?:[^'\\\n]|\\.|\\\n)*'|"(?:[^"\\\n]|\\.|\\\n)*")
  | (?P<istr>`(?:[^`\\]|\\.)*`)
  | (?P<num>0[xX][0-9a-fA-F_]+|0[bB][01_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.\.|\.\.=|//=|\.\.|==|~=|<=|>=|\+=|-=|\*=|/=|%=|\^=|//|::|->|[-+*/%^#<>=(){}\[\];:,.?|&])
""", re.S | re.X)


def tokenize(src):
    pos = 0
    line = 1
    out = []
    n = len(src)
    while pos < n:
        m = TOK.match(src, pos)
        if not m:
            raise SyntaxError("bad char at line %d: %r" % (line, src[pos:pos + 30]))
        k = m.lastgroup
        text = m.group(0)
        pos = m.end()
        if k == "nl":
            line += 1
            continue
        if k in ("ws",):
            continue
        if k in ("comment", "lcomment"):
            line += text.count("\n")
            continue
        if k == "long":
            body = m.group("lbody")
            if body.startswith("\r\n"):
                body = body[2:]
            elif body.startswith("\n"):
                body = body[1:]
            out.append(("str", body.encode("utf-8"), line))
            line += text.count("\n")
        elif k == "str":
            out.append(("str", unescape(text[1:-1], text[0]), line))
            line += text.count("\n")
        elif k == "istr":
            out.append(("istr", text[1:-1], line))
            line += text.count("\n")
        elif k == "num":
            t = text.replace("_", "")
            if t[:2] in ("0x", "0X"):
                v = float(int(t, 16))
            elif t[:2] in ("0b", "0B"):
                v = float(int(t[2:], 2))
            else:
                v = float(t)
            out.append(("num", v, line))
        elif k == "name":
            if text in KEYWORDS:
                out.append(("kw", text, line))
            else:
                out.append(("name", text, line))
        else:
            out.append(("op", text, line))
    out.append(("eof", None, line))
    return out


BINPRI = {
    "or": (1, 1), "and": (2, 2),
    "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
    "..": (5, 4),  # right assoc
    "+": (6, 6), "-": (6, 6),
    "*": (7, 7), "/": (7, 7), "//": (7, 7), "%": (7, 7),
    "^": (10, 9),  # right assoc
}
UNARY_PRI = 8


class Parser:
    def __init__(self, src):
        self.t = tokenize(src)
        self.i = 0

    # helpers
    def peek(self, k=0):
        return self.t[self.i + k]

    def check(self, kind, val=None):
        tk = self.t[self.i]
        return tk[0] == kind and (val is None or tk[1] == val)

    def accept(self, kind, val=None):
        if self.check(kind, val):
            self.i += 1
            return True
        return False

    def expect(self, kind, val=None):
        tk = self.t[self.i]
        if tk[0] != kind or (val is not None and tk[1] != val):
            raise SyntaxError(f"line {tk[2]}: expected {kind} {val!r}, got {tk[0]} {tk[1]!r}")
        self.i += 1
        return tk

    def line(self):
        return self.t[self.i][2]

    # blocks
    def block(self):
        stmts = []
        while True:
            tk = self.t[self.i]
            if tk[0] == "eof" or (tk[0] == "kw" and tk[1] in ("end", "else", "elseif", "until")):
                break
            if tk[0] == "kw" and tk[1] == "return":
                stmts.append(self.retstat())
                break
            s = self.statement()
            if s is not None:
                stmts.append(s)
        return stmts

    def retstat(self):
        ln = self.line()
        self.expect("kw", "return")
        exprs = []
        if not (self.check("eof") or (self.peek()[0] == "kw" and self.peek()[1] in ("end", "else", "elseif", "until")) or self.check("op", ";")):
            exprs = self.exprlist()
        self.accept("op", ";")
        return Node("Return", exprs, line=ln)

    def statement(self):
        tk = self.t[self.i]
        ln = tk[2]
        if tk[0] == "op" and tk[1] == ";":
            self.i += 1
            return None
        if tk[0] == "kw":
            v = tk[1]
            if v == "if":
                return self.ifstat()
            if v == "while":
                self.i += 1
                cond = self.expr()
                self.expect("kw", "do")
                body = self.block()
                self.expect("kw", "end")
                return Node("While", cond, body, line=ln)
            if v == "do":
                self.i += 1
                body = self.block()
                self.expect("kw", "end")
                return Node("Do", body, line=ln)
            if v == "for":
                return self.forstat()
            if v == "repeat":
                self.i += 1
                body = self.block()
                self.expect("kw", "until")
                cond = self.expr()
                return Node("Repeat", body, cond, line=ln)
            if v == "function":
                self.i += 1
                name = Node("Name", self.expect("name")[1], line=ln)
                is_method = False
                while self.check("op", ".") or self.check("op", ":"):
                    op = self.t[self.i][1]
                    self.i += 1
                    key = self.expect("name")[1]
                    name = Node("Index", name, Node("Const", key.encode()), line=ln)
                    if op == ":":
                        is_method = True
                        break
                f = self.funcbody(ln, is_method)
                return Node("Assign", [name], [f], line=ln)
            if v == "local":
                self.i += 1
                if self.accept("kw", "function"):
                    name = self.expect("name")[1]
                    f = self.funcbody(ln, False)
                    return Node("LocalFunction", name, f, line=ln)
                names = []
                while True:
                    names.append(self.expect("name")[1])
                    if self.accept("op", ":"):
                        self.skip_type()
                    if self.check("op", "<"):  # attributes <const>
                        self.i += 1
                        self.expect("name")
                        self.expect("op", ">")
                    if not self.accept("op", ","):
                        break
                exprs = []
                if self.accept("op", "="):
                    exprs = self.exprlist()
                return Node("Local", names, exprs, line=ln)
            if v == "return":
                return self.retstat()
            if v == "break":
                self.i += 1
                return Node("Break", line=ln)
        if tk[0] == "name" and tk[1] == "continue":
            nxt = self.peek(1)
            if not (nxt[0] == "op" and nxt[1] in ("=", ".", "[", ":", "(", ",", "+=", "-=")):
                self.i += 1
                return Node("Continue", line=ln)
        # expression statement
        e = self.suffixedexp()
        if self.check("op", "=") or self.check("op", ","):
            targets = [e]
            while self.accept("op", ","):
                targets.append(self.suffixedexp())
            self.expect("op", "=")
            exprs = self.exprlist()
            return Node("Assign", targets, exprs, line=ln)
        tk = self.t[self.i]
        if tk[0] == "op" and tk[1] in ("+=", "-=", "*=", "/=", "//=", "%=", "^=", "..="):
            self.i += 1
            rhs = self.expr()
            return Node("CompoundAssign", tk[1][:-1], e, rhs, line=ln)
        if e.k not in ("Call", "Method"):
            raise SyntaxError(f"line {ln}: syntax error near {self.t[self.i]}")
        return Node("CallStat", e, line=ln)

    def skip_type(self):
        # very loose: skip a simple type annotation
        depth = 0
        while True:
            tk = self.t[self.i]
            if tk[0] == "op" and tk[1] in ("(", "{", "<"):
                depth += 1
            elif tk[0] == "op" and tk[1] in (")", "}", ">"):
                if depth == 0:
                    return
                depth -= 1
            elif depth == 0 and (tk[0] == "op" and tk[1] in (",", "=")):
                return
            self.i += 1

    def ifstat(self):
        ln = self.line()
        self.expect("kw", "if")
        clauses = []
        cond = self.expr()
        self.expect("kw", "then")
        body = self.block()
        clauses.append((cond, body))
        orelse = None
        while True:
            if self.accept("kw", "elseif"):
                c = self.expr()
                self.expect("kw", "then")
                b = self.block()
                clauses.append((c, b))
            elif self.accept("kw", "else"):
                orelse = self.block()
                self.expect("kw", "end")
                break
            else:
                self.expect("kw", "end")
                break
        # convert elseif chain into nested Ifs
        node_else = orelse
        for c, b in reversed(clauses[1:]):
            node_else = [Node("If", c, b, node_else, line=ln)]
        return Node("If", clauses[0][0], clauses[0][1], node_else, line=ln)

    def forstat(self):
        ln = self.line()
        self.expect("kw", "for")
        n1 = self.expect("name")[1]
        if self.accept("op", ":"):
            self.skip_type()
        if self.accept("op", "="):
            start = self.expr()
            self.expect("op", ",")
            stop = self.expr()
            step = None
            if self.accept("op", ","):
                step = self.expr()
            self.expect("kw", "do")
            body = self.block()
            self.expect("kw", "end")
            return Node("NumFor", n1, start, stop, step, body, line=ln)
        names = [n1]
        while self.accept("op", ","):
            names.append(self.expect("name")[1])
            if self.accept("op", ":"):
                self.skip_type()
        self.expect("kw", "in")
        exprs = self.exprlist()
        self.expect("kw", "do")
        body = self.block()
        self.expect("kw", "end")
        return Node("GenFor", names, exprs, body, line=ln)

    def funcbody(self, ln, is_method):
        self.expect("op", "(")
        params = []
        vararg = False
        if is_method:
            params.append("self")
        if not self.check("op", ")"):
            while True:
                if self.accept("op", "..."):
                    vararg = True
                    if self.accept("op", ":"):
                        self.skip_type()
                    break
                params.append(self.expect("name")[1])
                if self.accept("op", ":"):
                    self.skip_type()
                if not self.accept("op", ","):
                    break
        self.expect("op", ")")
        if self.accept("op", ":"):
            self.skip_type()
        body = self.block()
        self.expect("kw", "end")
        return Node("Function", params, vararg, body, line=ln)

    def exprlist(self):
        es = [self.expr()]
        while self.accept("op", ","):
            es.append(self.expr())
        return es

    def primaryexp(self):
        tk = self.t[self.i]
        if tk[0] == "name":
            self.i += 1
            return Node("Name", tk[1], line=tk[2])
        if tk[0] == "op" and tk[1] == "(":
            self.i += 1
            e = self.expr()
            self.expect("op", ")")
            return Node("Paren", e, line=tk[2])
        raise SyntaxError(f"line {tk[2]}: unexpected {tk[0]} {tk[1]!r}")

    def suffixedexp(self):
        e = self.primaryexp()
        while True:
            tk = self.t[self.i]
            if tk[0] == "op":
                if tk[1] == ".":
                    self.i += 1
                    name = self.expect("name")[1]
                    e = Node("Index", e, Node("Const", name.encode()), line=tk[2])
                    continue
                if tk[1] == "[":
                    self.i += 1
                    k = self.expr()
                    self.expect("op", "]")
                    e = Node("Index", e, k, line=tk[2])
                    continue
                if tk[1] == ":":
                    self.i += 1
                    name = self.expect("name")[1]
                    args = self.callargs()
                    e = Node("Method", e, name, args, line=tk[2])
                    continue
                if tk[1] in ("(", "{"):
                    args = self.callargs()
                    e = Node("Call", e, args, line=tk[2])
                    continue
            if tk[0] == "str":
                args = self.callargs()
                e = Node("Call", e, args, line=tk[2])
                continue
            return e

    def callargs(self):
        tk = self.t[self.i]
        if tk[0] == "str":
            self.i += 1
            return [Node("Const", tk[1])]
        if tk[0] == "op" and tk[1] == "{":
            return [self.tablecons()]
        self.expect("op", "(")
        if self.accept("op", ")"):
            return []
        args = self.exprlist()
        self.expect("op", ")")
        return args

    def tablecons(self):
        ln = self.line()
        self.expect("op", "{")
        items = []  # (kind, key, value): kind 'pos' or 'kv'
        while not self.check("op", "}"):
            if self.check("op", "["):
                self.i += 1
                k = self.expr()
                self.expect("op", "]")
                self.expect("op", "=")
                v = self.expr()
                items.append(("kv", k, v))
            elif self.check("name") and self.peek(1)[0] == "op" and self.peek(1)[1] == "=":
                k = Node("Const", self.t[self.i][1].encode())
                self.i += 2
                v = self.expr()
                items.append(("kv", k, v))
            else:
                items.append(("pos", None, self.expr()))
            if not (self.accept("op", ",") or self.accept("op", ";")):
                break
        self.expect("op", "}")
        return Node("Table", items, line=ln)

    def simpleexp(self):
        tk = self.t[self.i]
        k, v, ln = tk
        if k == "num":
            self.i += 1
            return Node("Const", v, line=ln)
        if k == "str":
            self.i += 1
            return Node("Const", v, line=ln)
        if k == "istr":
            self.i += 1
            return Node("IString", v, line=ln)
        if k == "kw":
            if v == "nil":
                self.i += 1
                return Node("Const", None, line=ln)
            if v == "true":
                self.i += 1
                return Node("Const", True, line=ln)
            if v == "false":
                self.i += 1
                return Node("Const", False, line=ln)
            if v == "function":
                self.i += 1
                return self.funcbody(ln, False)
            if v == "if":
                self.i += 1
                c = self.expr()
                self.expect("kw", "then")
                a = self.expr()
                branches = [(c, a)]
                while self.accept("kw", "elseif"):
                    c2 = self.expr()
                    self.expect("kw", "then")
                    branches.append((c2, self.expr()))
                self.expect("kw", "else")
                e = self.expr()
                for c2, a2 in reversed(branches):
                    e = Node("IfExpr", c2, a2, e, line=ln)
                return e
        if k == "op":
            if v == "...":
                self.i += 1
                return Node("Vararg", line=ln)
            if v == "{":
                return self.tablecons()
        return self.suffixedexp()

    def expr(self, limit=0):
        tk = self.t[self.i]
        if (tk[0] == "kw" and tk[1] == "not") or (tk[0] == "op" and tk[1] in ("-", "#")):
            self.i += 1
            operand = self.expr(UNARY_PRI)
            op = tk[1]
            if op == "-" and operand.k == "Const" and isinstance(operand.a[0], float):
                left = Node("Const", -operand.a[0], line=tk[2])
            else:
                left = Node("Unop", op, operand, line=tk[2])
        else:
            left = self.simpleexp()
        while True:
            tk = self.t[self.i]
            op = tk[1] if tk[0] in ("op", "kw") else None
            if op not in BINPRI:
                break
            lp, rp = BINPRI[op]
            if lp <= limit:
                break
            self.i += 1
            right = self.expr(rp)
            if op == "and":
                left = Node("And", left, right, line=tk[2])
            elif op == "or":
                left = Node("Or", left, right, line=tk[2])
            else:
                left = Node("Binop", op, left, right, line=tk[2])
        return left


def parse(src):
    p = Parser(src)
    b = p.block()
    if not p.check("eof"):
        raise SyntaxError(f"line {p.line()}: trailing tokens {p.peek()}")
    return b


if __name__ == "__main__":
    import sys, time, pickle
    sys.setrecursionlimit(100000)
    t = time.time()
    src = open(sys.argv[1], encoding="utf-8").read()
    ast = parse(src)
    print("parsed", len(ast), "stmts in", time.time() - t)
