"""Symbolic lifter: runs the Luraph interpreter closures with concrete pc/operands and symbolic registers,
producing an IR control-flow graph per VM prototype."""
import sys, os, pickle, math, collections
sys.setrecursionlimit(200000)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from luaparse import Node
from interp import (Interp, LuaTable, LuaFunction, Builtin, Scope, LuaError, BREAK, CONTINUE, key_norm, tonum,
                    lua_type)
from boot import load_ast, BootInterp, build_data
from luadata import Ref


# ---------------------------------------------------------------- symbolic values
class Sym:
    __slots__ = ()


class SE(Sym):
    """symbolic IR expression"""
    __slots__ = ("e",)

    def __init__(self, e):
        self.e = e

    def __repr__(self):
        return "SE" + repr(self.e)


class Special(Sym):
    __slots__ = ()

    def sig(self):
        return (type(self).__name__,)


class Registers(Special):
    __slots__ = ()


class Env(Special):
    __slots__ = ()


class Captures(Special):
    __slots__ = ()


class UpvalRef(Special):
    __slots__ = ("i",)

    def __init__(self, i):
        self.i = i

    def sig(self):
        return ("U", self.i)


class UpvalPart(Special):
    __slots__ = ("i", "part")

    def __init__(self, i, part):
        self.i = i
        self.part = part

    def sig(self):
        return ("UP", self.i, self.part)


class MultiSym(Special):
    """multiple values from a source: ('call', cid) or ('va',)"""
    __slots__ = ("src",)

    def __init__(self, src):
        self.src = src

    def sig(self):
        return ("M", self.src)


class CountSym(Special):
    __slots__ = ("src", "off")

    def __init__(self, src, off=0.0):
        self.src = src
        self.off = off

    def sig(self):
        return ("C", self.src, self.off)


class PackTable(Special):
    __slots__ = ("src",)

    def __init__(self, src):
        self.src = src

    def sig(self):
        return ("PT", self.src)


class GenForIter(Special):
    __slots__ = ("fid", "nvars")

    def __init__(self, fid, nvars):
        self.fid = fid
        self.nvars = nvars

    def sig(self):
        return ("FG", self.fid)


class LoopVar(Special):
    __slots__ = ("off",)

    def __init__(self, off):
        self.off = off

    def sig(self):
        return ("LV", self.off)


class ITable(LuaTable):
    """lifter-internal table created by the interpreter code"""
    __slots__ = ("escaped",)

    def __init__(self, h=None):
        super().__init__(h)
        self.escaped = False


class LoopHead(Exception):
    pass


class LiftError(Exception):
    pass


ATOM_KINDS = {"k", "t", "v", "p"}


def is_atom(e):
    return e[0] in ATOM_KINDS


class TrackDict(dict):
    __slots__ = ("reads", "writes")

    def __init__(self, *a):
        super().__init__(*a)
        self.reads = set()
        self.writes = set()

    def __getitem__(self, k):
        if k not in self.writes:
            self.reads.add(k)
        return dict.__getitem__(self, k)

    def __setitem__(self, k, v):
        self.writes.add(k)
        dict.__setitem__(self, k, v)


class PathCtx:
    def __init__(self, decisions):
        self.decisions = decisions
        self.taken = []
        self.alts = []
        self.ir = []  # (depth, stmt)
        self.conds = []
        self.tmpn = 0


# ---------------------------------------------------------------- the symbolic interpreter
class SymInterp(BootInterp):
    def __init__(self):
        super().__init__()
        self.ctx = None
        self.callid = 0
        self.warnings = collections.Counter()
        self.main_loop_node = None
        self.journal = []
        self.mode = None
        self.vmstate = None

    # ---- IR helpers
    def emit(self, st):
        self.ctx.ir.append((len(self.ctx.taken), st))

    def newtmp(self):
        self.ctx.tmpn += 1
        return ("t", self.blockid, self.ctx.tmpn)

    def newcall(self):
        self.ctx.tmpn += 1
        return (self.blockid, self.ctx.tmpn)

    def warn(self, msg):
        self.warnings[msg] += 1

    def to_expr(self, v):
        if v is None or v is True or v is False:
            return ("k", v)
        if isinstance(v, (float, bytes)):
            return ("k", v)
        if isinstance(v, int):
            return ("k", float(v))
        if isinstance(v, SE):
            return v.e
        if isinstance(v, UpvalRef):
            return ("u", v.i)
        if isinstance(v, UpvalPart):
            return ("idx", ("u", v.i), ("k", float(v.part)))
        if isinstance(v, MultiSym):
            return ("sel", v.src, 1)
        if isinstance(v, CountSym):
            e = ("count", v.src)
            if v.off:
                e = ("bin", "+", e, ("k", v.off))
            return e
        if isinstance(v, ITable):
            return self.escape_table(v)
        if isinstance(v, LuaFunction):
            return ("hostfn", v.name or "", v.node.line)
        if isinstance(v, Builtin):
            return ("builtin", v.name)
        if isinstance(v, LuaTable):
            return ("static", v.name or hex(id(v)))
        raise LiftError("cannot convert to expr: " + repr(v))

    def escape_table(self, t):
        if t.escaped:
            self.warn("table escaped twice")
        t.escaped = True
        arr = []
        hashp = []
        n = 1
        while float(n) in t.h:
            arr.append(self.to_expr(t.h[float(n)]))
            n += 1
        for k, v in t.h.items():
            if isinstance(k, float) and k.is_integer() and 1 <= k < n:
                continue
            hashp.append((self.to_expr(k), self.to_expr(v)))
        tmp = self.newtmp()
        self.emit(("set", tmp, ("newtable", tuple(arr), tuple(hashp))))
        return tmp

    def mat(self, v):
        """materialize a value being stored into an interpreter local"""
        if isinstance(v, SE) and not is_atom(v.e):
            t = self.newtmp()
            self.emit(("set", t, v.e))
            return SE(t)
        if isinstance(v, MultiSym):
            # first value of a multi
            t = self.newtmp()
            self.emit(("set", t, self.to_expr(v)))
            return SE(t)
        if isinstance(v, UpvalPart) or isinstance(v, UpvalRef):
            return v
        return v

    def args_exprs(self, args):
        out = []
        for i, a in enumerate(args):
            if isinstance(a, MultiSym) and i == len(args) - 1:
                out.append(("multi", a.src))
            else:
                out.append(self.to_expr(a))
        return tuple(out)

    # ---- decisions
    def test(self, v):
        if v is None or v is False:
            return False
        if isinstance(v, Sym):
            if isinstance(v, (Registers, Env, Captures, PackTable, GenForIter)):
                return True
            if isinstance(v, SE) and v.e[0] in ("closure", "native", "newtable"):
                return True
            if isinstance(v, CountSym):
                return True
            cond = self.to_expr(v)
            if cond[0] == "k":
                return cond[1] is not None and cond[1] is not False
            ctx = self.ctx
            pos = len(ctx.taken)
            if pos < len(ctx.decisions):
                d = ctx.decisions[pos]
            else:
                d = True
                ctx.alts.append(ctx.taken + [False])
            ctx.taken.append(d)
            ctx.conds.append(cond)
            if len(ctx.taken) > 60:
                raise LiftError("too deep fork")
            return d
        return True

    # ---- core op overrides
    def index(self, obj, key):
        if isinstance(obj, Sym):
            if isinstance(obj, Registers):
                if isinstance(key, float):
                    return SE(("r", int(key)))
                if isinstance(key, LoopVar):
                    return SE(("rL", key.off))
                raise LiftError("register index not concrete: %r" % (key,))
            if isinstance(obj, Env):
                return SE(("g", self.to_expr(key)))
            if isinstance(obj, Captures):
                if isinstance(key, float):
                    return UpvalRef(int(key))
                raise LiftError("captures index not concrete")
            if isinstance(obj, UpvalRef):
                if key == 3.0 or key == 2.0:
                    return UpvalPart(obj.i, int(key))
                return SE(("idx", ("u", obj.i), self.to_expr(key)))
            if isinstance(obj, UpvalPart):
                if obj.part == 3 and isinstance(key, UpvalPart) and key.part == 2 and key.i == obj.i:
                    return SE(("u", obj.i))
                return SE(("idx", self.to_expr(obj), self.to_expr(key)))
            if isinstance(obj, PackTable):
                if isinstance(key, float):
                    i = int(key)
                    if obj.src == ("va",):
                        return SE(("p", i))
                    return SE(("sel", obj.src, i))
                if isinstance(key, LoopVar):
                    return SE(("selL", obj.src, key.off))
                raise LiftError("packtable index not concrete")
            if isinstance(obj, SE):
                return SE(("idx", obj.e, self.to_expr(key)))
            if isinstance(obj, MultiSym):
                return SE(("idx", self.to_expr(obj), self.to_expr(key)))
            raise LiftError("index on special %r" % (obj,))
        if isinstance(key, Sym) and isinstance(obj, LuaTable):
            if isinstance(key, SE) and key.e[0] == "k":
                return super().index(obj, key.e[1])
            raise LiftError("concrete table indexed with symbolic key %r %r" % (obj, key))
        return super().index(obj, key)

    def setindex(self, obj, key, val):
        if isinstance(obj, Sym):
            if isinstance(obj, Registers):
                if isinstance(key, float):
                    self.emit(("set", ("r", int(key)), self.to_expr(val)))
                    return
                if isinstance(key, LoopVar):
                    self.emit(("set", ("rL", key.off), self.to_expr(val)))
                    return
                raise LiftError("register store index not concrete")
            if isinstance(obj, Env):
                self.emit(("set", ("g", self.to_expr(key)), self.to_expr(val)))
                return
            if isinstance(obj, UpvalPart):
                if obj.part == 3 and isinstance(key, UpvalPart) and key.part == 2 and key.i == obj.i:
                    self.emit(("set", ("u", obj.i), self.to_expr(val)))
                    return
                self.emit(("set", ("idx", self.to_expr(obj), self.to_expr(key)), self.to_expr(val)))
                return
            if isinstance(obj, (SE, UpvalRef, MultiSym)):
                self.emit(("set", ("idx", self.to_expr(obj), self.to_expr(key)), self.to_expr(val)))
                return
            raise LiftError("setindex on special %r" % (obj,))
        if isinstance(obj, ITable) and obj.escaped:
            self.warn("write to escaped table")
        if isinstance(key, Sym) and isinstance(obj, LuaTable):
            if isinstance(key, SE) and key.e[0] == "k":
                return super().setindex(obj, key.e[1], val)
            raise LiftError("concrete table store with symbolic key %r" % (key,))
        if isinstance(obj, LuaTable) and not isinstance(obj, ITable) and obj is not self.G:
            if obj is self.vmstate:
                self.warn("vmState write %r" % (key,))
            elif obj.name and obj.name.startswith("data["):
                if self.mode is not None:
                    kk = key_norm(key)
                    self.journal.append((obj, kk, obj.h.get(kk)))
                    self.warn("program data write")
        return super().setindex(obj, key, val)

    def call(self, f, args):
        if isinstance(f, Sym):
            if isinstance(f, GenForIter):
                if len(args) >= 3:
                    self.emit(("fginit", f.fid, self.to_expr(args[0]), self.to_expr(args[1]), self.to_expr(args[2])))
                    return []
                if len(args) == 0:
                    self.emit(("fgnext", f.fid, f.nvars))
                    return [SE(("fgok", f.fid))] + [SE(("fgv", f.fid, i + 1)) for i in range(f.nvars)]
                raise LiftError("bad generic-for iterator call")
            fe = self.to_expr(f)
            cid = self.newcall()
            self.emit(("call", cid, fe, self.args_exprs(args)))
            return [MultiSym(("call", cid))]
        return super().call(f, args)

    def call_lua(self, f, args):
        args = [self.mat(a) for a in args]
        return super().call_lua(f, args)

    def binop(self, op, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return self.sym_binop(op, a, b)
        return super().binop(op, a, b)

    def sym_binop(self, op, a, b):
        # count arithmetic
        if isinstance(a, CountSym) and isinstance(b, float):
            if op == "+":
                return CountSym(a.src, a.off + b)
            if op == "-":
                return CountSym(a.src, a.off - b)
        if isinstance(b, CountSym) and isinstance(a, float) and op == "+":
            return CountSym(b.src, b.off + a)
        if isinstance(a, LoopVar) and isinstance(b, float):
            if op == "+":
                return LoopVar(a.off + b)
            if op == "-":
                return LoopVar(a.off - b)
        if isinstance(b, LoopVar) and isinstance(a, float) and op == "+":
            return LoopVar(b.off + a)
        if isinstance(a, CountSym) or isinstance(b, CountSym):
            if op in ("==", "~=", "<", "<=", ">", ">="):
                return SE(("bin", op, self.to_expr(a), self.to_expr(b)))
        if isinstance(a, (Registers, Env, Captures, GenForIter, PackTable)) or isinstance(b, (Registers, Env, Captures, GenForIter, PackTable)):
            if op == "==":
                return a is b or (isinstance(a, Special) and isinstance(b, Special) and a.sig() == b.sig())
            if op == "~=":
                return not (a is b or (isinstance(a, Special) and isinstance(b, Special) and a.sig() == b.sig()))
            raise LiftError("arith on special")
        ea = self.to_expr(a)
        eb = self.to_expr(b)
        if ea[0] == "k" and eb[0] == "k":
            return super().binop(op, ea[1], eb[1])
        return SE(("bin", op, ea, eb))

    def eq(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            r = self.sym_binop("==", a, b)
            return r
        return super().eq(a, b)

    def lt(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return self.sym_binop("<", a, b)
        return super().lt(a, b)

    def le(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return self.sym_binop("<=", a, b)
        return super().le(a, b)

    def concat(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return SE(("bin", "..", self.to_expr(a), self.to_expr(b)))
        return super().concat(a, b)

    def length(self, v):
        if isinstance(v, Sym):
            return SE(("un", "#", self.to_expr(v)))
        return super().length(v)

    def neg(self, v):
        if isinstance(v, Sym):
            return SE(("un", "-", self.to_expr(v)))
        return super().neg(v)

    def eval(self, e, sc):
        k = e.k
        if k == "Unop" and e.a[0] == "not":
            v = self.eval(e.a[1], sc)
            if isinstance(v, Sym) and not isinstance(v, (Registers, Env, Captures, PackTable, GenForIter, CountSym)):
                ex = self.to_expr(v)
                if not (isinstance(v, SE) and v.e[0] in ("closure", "native", "newtable")):
                    return SE(("un", "not", ex))
            return not self.test(v)
        if k in ("And", "Or"):
            a = self.eval(e.a[0], sc)
            if isinstance(a, Sym) and not isinstance(a, (Registers, Env, Captures, PackTable, GenForIter, CountSym)):
                n_ir = len(self.ctx.ir)
                b = self.eval(e.a[1], sc)
                if len(self.ctx.ir) == n_ir:
                    return SE(("and" if k == "And" else "or", self.to_expr(a), self.to_expr(b)))
                raise LiftError("side effects in symbolic and/or")
            return super().eval(e, sc)
        if k in ("Call", "Method", "Vararg"):
            r = self.eval_multi(e, sc)
            v = r[0] if r else None
            return v
        if k == "Table":
            return self.eval_table(e, sc)
        return super().eval(e, sc)

    def eval_table(self, e, sc):
        t = ITable()
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
                    if vals and isinstance(vals[-1], MultiSym):
                        if len(vals) == 1 and n == 1 and len(items) == 1:
                            return PackTable(vals[-1].src)
                        raise LiftError("table with multi values")
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

    def rawset(self, t, k, v):
        if isinstance(k, Sym):
            if isinstance(k, SE) and k.e[0] == "k":
                k = k.e[1]
            else:
                raise LiftError("rawset symbolic key")
        return super().rawset(t, k, v)

    # local-variable assignment materialization
    def exec_stmt(self, s, sc):
        k = s.k
        if s is self.main_loop_node and self.mode == "entry":
            raise LoopHead()
        if k == "Local":
            names, exprs = s.a
            vals = self.eval_args(exprs, sc) if exprs else []
            for i, n in enumerate(names):
                sc.vars[n] = self.mat(vals[i]) if i < len(vals) else None
            return None
        if k == "Assign":
            targets, exprs = s.a
            if all(t.k == "Name" for t in targets):
                vals = self.eval_args(exprs, sc)
                for i in range(len(targets) - 1, -1, -1):
                    v = vals[i] if i < len(vals) else None
                    self.set_var(targets[i].a[0], self.mat(v), sc)
                return None
            if len(targets) == 1 and len(exprs) == 1:
                t = targets[0]
                o = self.eval(t.a[0], sc)
                key = self.eval(t.a[1], sc)
                val = self.eval(exprs[0], sc)
                self.setindex(o, key, val)
                return None
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
                    self.set_var(t.a[0], self.mat(v), sc)
                else:
                    self.setindex(pre[i][0], pre[i][1], v)
            return None
        if k == "CompoundAssign":
            op, t, rhs = s.a
            if t.k == "Name":
                cur = self.lookup_var(t.a[0], sc)
                self.set_var(t.a[0], self.mat(self.binop(op, cur, self.eval(rhs, sc))), sc)
                return None
            return super().exec_stmt(s, sc)
        if k == "NumFor":
            var, e1, e2, e3, body = s.a
            a = self.eval(e1, sc)
            b = self.eval(e2, sc)
            st = self.eval(e3, sc) if e3 is not None else 1.0
            if isinstance(a, Sym) or isinstance(b, Sym) or isinstance(st, Sym):
                return self.sym_loop(var, a, b, st, body, sc)
            return super().exec_stmt(s, sc)
        if k == "GenFor":
            names, exprs, body = s.a
            vals = self.eval_args(exprs, sc)
            if vals and isinstance(vals[0], Sym):
                raise LiftError("generic for over symbolic value inside handler")
            # re-evaluate via base implementation (exprs have no side effects here)
            return super().exec_stmt(s, sc)
        return super().exec_stmt(s, sc)

    def sym_loop(self, var, a, b, st, body, sc):
        """summarize a loop with symbolic bounds (multret copies)"""
        if not (isinstance(a, float) and st == 1.0):
            raise LiftError("symbolic loop start/step")
        sub_start = len(self.ctx.ir)
        inner = Scope(sc)
        inner.vars[var] = LoopVar(a)
        r = self.exec_block(body, inner)
        if r is not None:
            raise LiftError("control flow in symbolic loop")
        stmts = self.ctx.ir[sub_start:]
        del self.ctx.ir[sub_start:]
        regs = [st_ for d, st_ in stmts if not (st_[0] == "set" and st_[1][0] == "t")]
        if isinstance(b, CountSym):
            # iterations: o from a to count+off  -> n = count + off - a + 1
            adj = b.off - a + 1
            if len(regs) == 1 and regs[0][0] == "set" and regs[0][1][0] == "rL" and regs[0][2][0] in ("selL", "sel", "p"):
                dst_off = regs[0][1][1]  # absolute register index for iteration o=a  (LoopVar off = a + k)
                val = regs[0][2]
                if val[0] == "p":
                    src, src_first = ("va",), val[1]
                else:
                    src = val[1]
                    src_first = val[2]
                if src_first != 1.0 or adj != 0:
                    raise LiftError("unexpected multret copy shape %r %r" % (src_first, adj))
                if src != b.src:
                    raise LiftError("multret copy source mismatch")
                self.emit(("multicopy", int(dst_off), src))
                return None
            if len(regs) == 1 and regs[0][0] == "set" and regs[0][1][0] == "rL" and regs[0][2][0] == "p" and False:
                pass
        raise LiftError("unrecognized symbolic loop: %r bounds %r %r" % (stmts, a, b))

    def eval_multi(self, e, sc):
        if e.k == "Vararg":
            return list(sc.varargs)
        return super().eval_multi(e, sc)

    # ---- setup
    def install_models(self, state, runtime, native_tbl):
        self.vmstate = state
        I = self
        vm = state.h
        self.real_factory = vm[56.0]

        def unpack_model(tbl, i, j):
            if isinstance(tbl, Registers):
                if isinstance(j, CountSym):
                    # registers i .. j where values from off+1 are multi(src)
                    base = j.off + 1
                    out = [SE(("r", int(r))) for r in range(int(i), int(base))]
                    return out + [MultiSym(j.src)]
                if not isinstance(i, float) or not isinstance(j, float):
                    raise LiftError("unpack bounds symbolic %r %r" % (i, j))
                return [SE(("r", int(r))) for r in range(int(i), int(j) + 1)]
            if isinstance(tbl, PackTable):
                if isinstance(j, CountSym) and j.off == 0 and i == 1.0:
                    return [MultiSym(tbl.src)]
                raise LiftError("unpack packtable %r %r" % (i, j))
            if isinstance(tbl, LuaTable):
                ii = int(i) if i is not None else 1
                jj = int(j) if j is not None else tbl.length()
                return [tbl.h.get(float(k)) for k in range(ii, jj + 1)]
            raise LiftError("unpack of %r" % (tbl,))

        # vmState[27](from, to, tbl) ; vmState[26](to, from, tbl) ; vmState[4] = unpack(tbl, from, to)
        vm[27.0] = Builtin(lambda a: unpack_model(a[2], a[0] if a[0] is not None else 1.0, a[1]), "unpack27")
        vm[26.0] = Builtin(lambda a: unpack_model(a[2], a[1], a[0]), "unpack26")
        vm[4.0] = Builtin(lambda a: unpack_model(a[0], a[1] if len(a) > 1 else 1.0, a[2] if len(a) > 2 else None), "unpack")

        def pack_model(a):
            if a and isinstance(a[-1], MultiSym):
                if len(a) == 1:
                    return [CountSym(a[0].src), PackTable(a[0].src)]
                raise LiftError("pack with prefix + multi")
            t = ITable({float(i + 1): v for i, v in enumerate(a)})
            return [float(len(a)), t]
        vm[55.0] = Builtin(pack_model, "pack55")

        def select_model(a):
            if a[0] == b"#":
                if len(a) == 2 and isinstance(a[1], MultiSym):
                    return [CountSym(a[1].src)]
                if any(isinstance(x, MultiSym) for x in a[1:]):
                    raise LiftError("select # complex")
                return [float(len(a) - 1)]
            n = a[0]
            if isinstance(n, float):
                rest = a[1:]
                if len(rest) == 1 and isinstance(rest[0], MultiSym):
                    if rest[0].src == ("va",):
                        raise LiftError("select(n, ...) on varargs")
                return rest[int(n) - 1:]
            raise LiftError("select model")
        vm[29.0] = Builtin(select_model, "select")
        def newregs(a):
            if self.mode == "entry" and not getattr(self.ctx, "regs_made", False):
                self.ctx.regs_made = True
                return [Registers()]
            t = self.newtmp()
            self.emit(("set", t, ("newtable", (), ())))
            return [SE(t)]
        vm[10.0] = Builtin(newregs, "table.create")
        vm[30.0] = Builtin(lambda a: [Env()], "getfenv")
        vm[31.0] = Builtin(lambda a: [a[0]], "setfenv")

        def newtable_model(a):
            t = self.newtmp()
            self.emit(("set", t, ("newtable", (), ())))
            return [SE(t)]
        vm[12.0] = Builtin(newtable_model, "table.create")

        def move_model(a):
            src, f, e, t = a[0], a[1], a[2], a[3]
            dst = a[4] if len(a) > 4 else src
            if isinstance(src, Registers):
                vals = unpack_model(src, f, e)
                self.emit(("setlist", self.to_expr(dst), self.to_expr(t), self.args_exprs(vals)))
                return [dst]
            raise LiftError("table.move model on %r" % (src,))
        vm[11.0] = Builtin(move_model, "table.move")

        def wrap_model(a):
            fn = a[0]
            # count loop variables of the generic for inside fn
            nv = 2
            for st in fn.node.a[2]:
                if st.k == "GenFor":
                    nv = len(st.a[0])
            pc = self.cur_pc()
            return [GenForIter(("fg", self.protoid, pc), nv)]
        vm[1.0] = Builtin(wrap_model, "coroutine.wrap")

        def closure_model(a):
            proto, D = a[0], a[1]
            pid = self.proto_index.get(id(proto))
            if pid is None:
                raise LiftError("closure of unknown proto")
            return [SE(("closure", pid, self.upval_refs(D)))]
        vm[56.0] = Builtin(closure_model, "newclosure")
        # natives
        names = {}
        for k, v in native_tbl.h.items():
            names[id(v)] = k.decode()
        for k, v in list(runtime.h.items()):
            if id(v) in names:
                nm = names[id(v)]
                runtime.h[k] = Builtin((lambda nm: (lambda a: [SE(("native", nm, self.upval_refs(a[0])))]))(nm), "native:" + nm)

    def upval_refs(self, D):
        refs = []
        if D is None or D is False:
            return ()
        if not isinstance(D, LuaTable):
            raise LiftError("upvalue list not a table")
        n = 0
        maxk = max([int(k) for k in D.h if isinstance(k, float)], default=-1)
        for i in range(0, maxk + 1):
            v = D.h.get(float(i))
            if isinstance(v, ITable) and isinstance(v.h.get(3.0), Registers):
                refs.append(("rref", int(v.h[2.0])))
            elif isinstance(v, UpvalRef):
                refs.append(("u", v.i))
            elif v is None:
                refs.append(("k", None))
            else:
                refs.append(("val", self.to_expr(v)))
        return tuple(refs)

    def cur_pc(self):
        v = self.fscope.vars
        return dict.get(v, "pc")


# ---------------------------------------------------------------- driver
class Lifter:
    def __init__(self):
        self.I = SymInterp()
        I = self.I
        ast = load_ast()
        Backend, Native, control = I.run_chunk(ast)
        runtime = I.call(I.index(Backend, b"createRuntime"), [])[0]
        state = I.call(I.index(Backend, b"bindPrimitives"), [runtime])[0]
        C, count, F, metas = pickle.load(open(os.path.join(HERE, "progdata.pkl"), "rb"))
        tables, consts = build_data(I, C, F, metas)
        cm = LuaTable(name="constmeta")

        def cindex(a):
            ops, pc = a[0], a[1]
            idx = ops.h[0.0].h.get(key_norm(pc))
            return [consts.get(idx)]
        cm.h[b"__index"] = Builtin(cindex, "constindex")
        for n in metas:
            tables[n].meta = cm
        self.data = LuaTable({float(i): tables[i] for i in tables})
        I.call(I.index(Backend, b"initialize"), [self.data])
        self.tables = tables
        self.consts = consts
        self.F = F
        self.protos = {n: t for n, t in F.items() if set(t.keys()) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
                       and isinstance(t[3], Ref) and not isinstance(t[1], Ref)}
        I.proto_index = {id(tables[n]): n for n in self.protos}
        I.install_models(state, runtime, Native)
        self.state = state

    def lift(self, pid, verbose=False):
        I = self.I
        proto = self.tables[pid]
        I.protoid = pid
        I.blockid = 0
        I.ctx = PathCtx([])
        f = I.call(I.real_factory, [proto, Captures()])[0]
        if not isinstance(f, LuaFunction):
            raise LiftError("factory did not return a lua function")
        fnode = f.node
        params, vararg, body = fnode.a
        # find main loop
        main = None
        for s in body:
            if s.k in ("While", "Repeat"):
                main = s
                break
        I.main_loop_node = main
        blocks = {}
        variants = collections.defaultdict(list)
        worklist = []

        def make_scope():
            sc = Scope(f.scope)
            sc.vars = TrackDict()
            sc.varargs = [MultiSym(("va",))]
            return sc

        def snapshot(vars_):
            memo = {}

            def cp(v):
                if isinstance(v, ITable):
                    if id(v) in memo:
                        return memo[id(v)]
                    t = ITable()
                    memo[id(v)] = t
                    t.escaped = v.escaped
                    t.meta = v.meta
                    for k2, v2 in v.h.items():
                        t.h[k2] = cp(v2)
                    return t
                return v
            return {k: cp(v) for k, v in dict.items(vars_)}

        def sig(v):
            if v is None or v is True or v is False:
                return ("c", v)
            if isinstance(v, (float, bytes)):
                return ("c", v)
            if isinstance(v, SE):
                return ("e", v.e)
            if isinstance(v, Special):
                return v.sig()
            if isinstance(v, ITable):
                return ("T",)
            return ("o", id(v))

        def spill(vars_, pc):
            for k, v in list(dict.items(vars_)):
                if isinstance(v, SE) and v.e[0] != "k":
                    name = ("v", int(pc), k)
                    if v.e != name:
                        I.emit(("set", name, v.e))
                    dict.__setitem__(vars_, k, SE(name))

        def run_paths(start_vars, mode, bid):
            """execute one block (all paths). returns tree and live-in set"""
            I.blockid = bid
            stack = [[]]
            paths = []
            livein = set()
            while stack:
                dec = stack.pop()
                ctx = PathCtx(dec)
                I.ctx = ctx
                sc = make_scope()
                if start_vars is not None:
                    for k2, v2 in snapshot(start_vars).items():
                        dict.__setitem__(sc.vars, k2, v2)
                    sc.vars.reads = set()
                    sc.vars.writes = set()
                I.fscope = sc
                I.mode = mode
                outcome = None
                try:
                    if mode == "entry":
                        try:
                            r = I.exec_block(body, sc)
                            outcome = ("ret", list(r[1]) if r and r not in (BREAK, CONTINUE) else [])
                        except LoopHead:
                            outcome = ("head",)
                    else:
                        loop_body = main.a[0] if main.k == "Repeat" else main.a[1]
                        inner = Scope(sc)
                        r = I.exec_block(loop_body, inner)
                        if r is None or r is CONTINUE:
                            outcome = ("head",)
                        elif r is BREAK:
                            idx = body.index(main)
                            r2 = I.exec_block(body[idx + 1:], sc)
                            outcome = ("ret", list(r2[1]) if r2 and r2 not in (BREAK, CONTINUE) else [])
                        else:
                            outcome = ("ret", list(r[1]))
                except LuaError as e:
                    outcome = ("err", repr(e.value) + " @line " + str(getattr(e, "line", None)))
                except LiftError as e:
                    outcome = ("lifterr", str(e))
                except RecursionError:
                    outcome = ("lifterr", "recursion")
                livein |= sc.vars.reads
                if outcome[0] == "head":
                    pc = dict.get(sc.vars, "pc")
                    spill(sc.vars, pc)
                    outcome = ("head", dict(sc.vars))
                elif outcome[0] == "ret":
                    try:
                        outcome = ("ret", I.args_exprs(outcome[1]))
                    except LiftError as e:
                        outcome = ("lifterr", str(e))
                paths.append((ctx.taken, ctx.conds, ctx.ir, outcome))
                for alt in ctx.alts:
                    stack.append(alt)
                if len(paths) > 20000:
                    raise LiftError("path explosion in block %d" % bid)
            return paths, livein

        def find_or_add(vars_):
            pc = vars_.get("pc")
            for (names, sg, bid) in variants[pc]:
                if all(sig(vars_.get(n)) == s for n, s in zip(names, sg)):
                    return bid
            bid = len(blocks) + 1
            blocks[bid] = None
            # placeholder until processed; live-in set unknown until run
            variants[pc].append((None, None, bid))
            worklist.append((bid, vars_))
            return bid

        # entry
        paths, livein = run_paths(None, "entry", 0)
        blocks[0] = {"pc": None, "paths": paths}
        self._resolve(paths, find_or_add)
        while worklist:
            bid, vars_ = worklist.pop()
            paths, livein = run_paths(vars_, "loop", bid)
            pc = vars_.get("pc")
            names = tuple(sorted(livein))
            # fix the variant entry
            lst = variants[pc]
            for i, (nm, sg, b) in enumerate(lst):
                if b == bid:
                    lst[i] = (names, tuple(sig(vars_.get(n)) for n in names), bid)
            blocks[bid] = {"pc": pc, "paths": paths}
            self._resolve(paths, find_or_add)
        return blocks

    def _resolve(self, paths, find_or_add):
        for i, (taken, conds, ir, outcome) in enumerate(paths):
            if outcome[0] == "head":
                vars_ = outcome[1]
                bid = find_or_add(vars_)
                paths[i] = (taken, conds, ir, ("goto", bid))


def build_tree(paths):
    """turn list of paths into a decision tree: node = (stmts, term) where term is
    ('if', cond, node_t, node_f) or leaf outcome"""
    def rec(ps, depth):
        # statements emitted at this depth (common to all ps)
        first = ps[0]
        stmts = [st for d, st in first[2] if d == depth]
        # sanity: compare against others
        for p in ps[1:]:
            other = [st for d, st in p[2] if d == depth]
            if other != stmts:
                raise LiftError("divergent common prefix at depth %d" % depth)
        if len(first[0]) == depth:
            assert len(ps) == 1, "multiple paths ending at same depth"
            return (stmts, first[3])
        cond = first[1][depth]
        tps = [p for p in ps if p[0][depth] is True]
        fps = [p for p in ps if p[0][depth] is False]
        return (stmts, ("if", cond, rec(tps, depth + 1) if tps else None, rec(fps, depth + 1) if fps else None))
    return rec(paths, 0)


if __name__ == "__main__":
    import time
    t0 = time.time()
    L = Lifter()
    print("setup", time.time() - t0)
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    t0 = time.time()
    blocks = L.lift(pid)
    print("lifted", len(blocks), "blocks in", time.time() - t0)
    print(L.I.warnings)
    errs = collections.Counter()
    for bid, b in blocks.items():
        for taken, conds, ir, out in b["paths"]:
            if out[0] in ("err", "lifterr"):
                errs[out[1][:200]] += 1
    for e, c in errs.most_common(30):
        print(c, e)
