"""Symbolic lifter v2: abstract interpretation of the Luraph interpreter closures.

Registers and interpreter-local data values may hold known constants (KVal); block states are merged
(meet) at join points and blocks are re-lifted until a fixpoint. VM-level writes into the program's own
bytecode tables (jump resolvers) are applied persistently and invalidate dependent blocks.
"""
import sys, os, pickle, math, collections
sys.setrecursionlimit(200000)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from luaparse import Node
from interp import (Interp, LuaTable, LuaFunction, Builtin, Scope, LuaError, BREAK, CONTINUE, key_norm, tonum,
                    lua_type, truthy)
from boot import load_ast, BootInterp, build_data
from luadata import Ref
from struct import error as struct_error


# ---------------------------------------------------------------- symbolic values
class Sym:
    __slots__ = ()


class SE(Sym):
    __slots__ = ("e",)

    def __init__(self, e):
        self.e = e

    def __repr__(self):
        return "SE" + repr(self.e)


class KVal(Sym):
    """a known (constant) data value"""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return "K(%r)" % (self.v,)


class Special(Sym):
    __slots__ = ()

    def sig(self):
        return (type(self).__name__,)


class Registers(Special):
    __slots__ = ("known", "captured", "multi")

    def __init__(self):
        self.known = {}
        self.captured = set()
        self.multi = None

    def copy(self):
        r = Registers()
        r.known = dict(self.known)
        r.captured = set(self.captured)
        r.multi = self.multi
        return r


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


class LazyRefs:
    __slots__ = ("D", "resolved")

    def __init__(self, D):
        self.D = D
        self.resolved = None

    def __repr__(self):
        return "LazyRefs"


class ClosureSym(Sym):
    __slots__ = ("kind", "pid", "D")

    def __init__(self, kind, pid, D):
        self.kind = kind
        self.pid = pid
        self.D = D


class ITable(LuaTable):
    __slots__ = ("escaped", "closed")

    def __init__(self, h=None):
        super().__init__(h)
        self.escaped = False
        self.closed = False


DEBUG_MEET = {}


class LoopHead(Exception):
    pass


class Tamper(Exception):
    pass


MISSING = object()


class _NilV:
    def __repr__(self):
        return "NILV"


NILV = _NilV()


class LiftError(Exception):
    pass


class Conflict(Exception):
    pass


ATOM_KINDS = {"k", "t", "v", "p", "builtin", "static", "hostfn"}
NOTRUTH = (Registers, Env, Captures, PackTable, GenForIter, CountSym)


def is_atom(e):
    return e[0] in ATOM_KINDS


def is_conc(v):
    return v is None or v is True or v is False or isinstance(v, (float, bytes, Builtin, LuaFunction)) or (
        isinstance(v, LuaTable) and not isinstance(v, ITable)) or type(v).__name__ == "Registers"


def uw(v):
    return v.v if isinstance(v, KVal) else v


def same(a, b):
    if a is b:
        return True
    if type(a) != type(b):
        return False
    if isinstance(a, (float, bytes)):
        return a == b or (a != a and b != b)
    return False


PURE_PREFIX = ("bit32.", "math.")
PURE_NAMES = {"string.sub", "string.len", "string.byte", "string.char", "string.rep", "string.lower",
              "string.upper", "string.reverse", "string.format", "string.unpack", "string.pack",
              "string.packsize", "tonumber", "tostring"}


def is_pure(b):
    n = b.name
    return n.startswith(PURE_PREFIX) or n in PURE_NAMES


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
        self.ir = []
        self.conds = []
        self.tmpn = 0
        self.regs_made = False


# ---------------------------------------------------------------- the symbolic interpreter
class SymInterp(BootInterp):
    def __init__(self):
        super().__init__()
        self.ctx = PathCtx([])
        self.warnings = collections.Counter()
        self.main_loop_node = None
        self.journal = []
        self.mode = None
        self.vmstate = None
        self.model_ids = set()
        self.cur_reads = None
        self.static_writes = []
        self.on_static_write = None
        self.patched = set()
        self.global_patch = {}
        self.patch_orig = {}
        self.trampolines = 0
        self.overlay_all = True
        self.lazies = []
        self.closure_slots = {}
        self.blockid = 0
        self.protoid = 0
        self.fscope = Scope(None)

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

    def kwrap(self, v):
        if isinstance(v, Sym) or isinstance(v, ITable):
            return v
        return KVal(v)

    def to_expr(self, v):
        if v is None or v is True or v is False:
            return ("k", v)
        if isinstance(v, (float, bytes)):
            return ("k", v)
        if isinstance(v, int):
            return ("k", float(v))
        if isinstance(v, SE):
            return v.e
        if isinstance(v, KVal):
            return self.to_expr(v.v)
        if isinstance(v, ClosureSym):
            lz = LazyRefs(v.D)
            self.lazies.append(lz)
            return (v.kind, v.pid, lz)
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
        if isinstance(v, Registers):
            return ("regfile",)
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
        if isinstance(v, SE) and not is_atom(v.e):
            t = self.newtmp()
            self.emit(("set", t, v.e))
            return SE(t)
        if isinstance(v, MultiSym):
            t = self.newtmp()
            self.emit(("set", t, self.to_expr(v)))
            return SE(t)
        return v

    def args_exprs(self, args):
        out = []
        for i, a in enumerate(args):
            if isinstance(a, MultiSym) and i == len(args) - 1:
                out.append(("multi", a.src))
            else:
                out.append(self.to_expr(a))
        return tuple(out)

    def note_read(self, t, k):
        if self.cur_reads is not None and t.name and t.name.startswith("data["):
            self.cur_reads.add((id(t), key_norm(k)))

    # ---- decisions
    def test(self, v):
        if v is None or v is False:
            return False
        if isinstance(v, Sym):
            if isinstance(v, KVal):
                return truthy(v.v)
            if isinstance(v, (NOTRUTH, ClosureSym)):
                return True
            if isinstance(v, SE) and v.e[0] in ("closure", "native", "newtable", "builtin", "static", "hostfn"):
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
            if len(ctx.taken) > 200:
                raise LiftError("too deep fork")
            return d
        return True

    # ---- core op overrides
    def index(self, obj, key):
        if isinstance(key, KVal):
            key = key.v
        if isinstance(obj, Sym):
            if isinstance(obj, KVal) and isinstance(obj.v, Registers):
                return self.index(obj.v, key)
            if isinstance(obj, KVal):
                inner = obj.v
                if isinstance(key, Sym):
                    if isinstance(inner, LuaTable):
                        return SE(("idx", self.to_expr(inner), self.to_expr(key)))
                    raise LiftError("known value indexed with symbolic key")
                if isinstance(inner, LuaTable):
                    self.note_read(inner, key)
                    ov = self.overlay_get(inner, key)
                    if ov is not MISSING:
                        return self.kwrap(None if ov is NILV else ov)
                    return self.kwrap(super().index(inner, key))
                return self.kwrap(super().index(inner, key))
            if isinstance(obj, Registers):
                if isinstance(key, float):
                    i = int(key)
                    if i in obj.known:
                        return KVal(obj.known[i])
                    return SE(("r", i))
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
                key = key.e[1]
            else:
                raise LiftError("concrete table indexed with symbolic key %r %r" % (obj, key))
        if isinstance(obj, LuaTable) and not isinstance(obj, ITable):
            self.note_read(obj, key)
            ov = self.overlay_get(obj, key)
            if ov is not MISSING:
                return None if ov is NILV else ov
        return super().index(obj, key)

    def overlay_get(self, t, key):
        kk = key_norm(key)
        if (id(t), kk) not in self.patched:
            return MISSING
        vars_ = self.fscope.vars
        name = ("mem", id(t), kk)
        if isinstance(vars_, TrackDict) and name not in vars_.writes:
            vars_.reads.add(name)
        return dict.get(vars_, name, MISSING)

    def setindex(self, obj, key, val):
        if isinstance(key, KVal):
            key = key.v
        if isinstance(obj, Sym):
            if isinstance(obj, KVal) and isinstance(obj.v, Registers):
                return self.setindex(obj.v, key, val)
            if isinstance(obj, KVal):
                inner = obj.v
                v = uw(val)
                if not isinstance(inner, LuaTable) or isinstance(v, Sym) or isinstance(key, Sym) or isinstance(v, ITable):
                    raise Tamper("write to program data %r[%r] = %r" % (inner, key, val))
                self.static_write(inner, key, v)
                return
            if isinstance(obj, Registers):
                if isinstance(key, float):
                    i = int(key)
                    self.emit(("set", ("r", i), self.to_expr(val)))
                    v = uw(val)
                    if is_conc(v) and i not in obj.captured:
                        obj.known[i] = v
                    else:
                        obj.known.pop(i, None)
                    return
                if isinstance(key, LoopVar):
                    self.emit(("set", ("rL", key.off), self.to_expr(val)))
                    return
                raise Tamper("dynamic register write")
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
        if isinstance(obj, ITable) and not obj.closed:
            kk2 = key_norm(uw(key)) if not isinstance(uw(key), Sym) else None
            if kk2 in (2.0, 3.0) and isinstance(obj.h.get(3.0), Registers) and isinstance(uw(obj.h.get(2.0)), float):
                nv = uw(val)
                if (kk2 == 3.0 and not isinstance(nv, Registers)) or (kk2 == 2.0 and not same(nv, uw(obj.h.get(2.0)))):
                    self.emit(("close", int(uw(obj.h[2.0]))))
                    obj.closed = True
        if isinstance(obj, ITable) and id(obj) in self.closure_slots and self.closure_slots[id(obj)][0] is obj:
            kk = key_norm(uw(key))
            self.closure_slots[id(obj)][1][kk] = self.ref_of(val)
        if isinstance(key, Sym) and isinstance(obj, LuaTable):
            if isinstance(key, SE) and key.e[0] == "k":
                key = key.e[1]
            else:
                raise LiftError("concrete table store with symbolic key %r" % (key,))
        if isinstance(obj, LuaTable) and not isinstance(obj, ITable) and obj is not self.G:
            if obj is self.vmstate:
                self.warn("vmState write %r" % (key,))
            elif obj.name and obj.name.startswith("data["):
                if self.mode is not None:
                    kk = key_norm(key)
                    self.journal.append((obj, kk, obj.h.get(kk)))
        return super().setindex(obj, key, val)

    def static_write(self, t, key, v):
        """VM-level write into the program's own tables.
        First value for a slot is applied globally (one-time resolver). A slot that later receives a different
        value becomes path-local (trampoline)."""
        kk = key_norm(key)
        k = (id(t), kk)
        self.static_writes.append((t.name, kk, v))
        if self.on_static_write:
            self.on_static_write(t, kk, "write")
        if self.overlay_all and k not in self.patched:
            self.patched.add(k)
            if self.on_static_write:
                self.on_static_write(t, kk, "change")
        if k in self.patched:
            name = ("mem", id(t), kk)
            self.fscope.vars[name] = NILV if v is None else v
            return
        if k in self.global_patch:
            if same(self.global_patch[k], v) or (self.global_patch[k] == v and type(self.global_patch[k]) == type(v)):
                return
            # becomes a trampoline: restore and switch to path-local
            orig = self.patch_orig[k]
            if orig is None:
                t.h.pop(kk, None)
            else:
                t.h[kk] = orig
            del self.global_patch[k]
            self.patched.add(k)
            self.trampolines += 1
            name = ("mem", id(t), kk)
            self.fscope.vars[name] = NILV if v is None else v
            if self.on_static_write:
                self.on_static_write(t, kk, "change")
            return
        self.patch_orig[k] = t.h.get(kk)
        self.global_patch[k] = v
        if v is None:
            t.h.pop(kk, None)
        else:
            t.h[kk] = v
        if self.on_static_write:
            self.on_static_write(t, kk, "change")

    def call(self, f, args):
        if isinstance(f, KVal):
            inner = f.v
            if isinstance(inner, (Builtin, LuaFunction)):
                if all(not isinstance(a, Sym) or isinstance(a, KVal) for a in args) and (
                        isinstance(inner, LuaFunction) or is_pure(inner)):
                    try:
                        r = super().call(inner, [uw(a) for a in args])
                        return [self.kwrap(x) for x in r]
                    except (LuaError, ValueError, TypeError, struct_error, OverflowError, IndexError):
                        pass
                fe = self.to_expr(inner)
                cid = self.newcall()
                self.emit(("call", cid, fe, self.args_exprs(args)))
                return [MultiSym(("call", cid))]
            raise LiftError("call of known non-function %r" % (inner,))
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
        if isinstance(f, Builtin) and id(f) not in self.model_ids:
            if any(isinstance(a, Sym) for a in args):
                if all(not isinstance(a, Sym) or isinstance(a, KVal) for a in args) and is_pure(f):
                    try:
                        r = super().call(f, [uw(a) for a in args])
                        return [self.kwrap(x) for x in r]
                    except (LuaError, ValueError, TypeError, struct_error, OverflowError, IndexError):
                        pass
                cid = self.newcall()
                self.emit(("call", cid, ("builtin", f.name), self.args_exprs(args)))
                return [MultiSym(("call", cid))]
        return super().call(f, args)

    def call_lua(self, f, args):
        args = [self.mat(a) for a in args]
        return super().call_lua(f, args)

    def binop(self, op, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            if isinstance(a, KVal) or isinstance(b, KVal):
                ua, ub = uw(a), uw(b)
                if not isinstance(ua, Sym) and not isinstance(ub, Sym):
                    return KVal(super().binop(op, ua, ub))
                a, b = ua, ub
            return self.sym_binop(op, a, b)
        return super().binop(op, a, b)

    def sym_binop(self, op, a, b):
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
            other = b if isinstance(a, CountSym) else a
            if op in ("==", "~="):
                if not isinstance(other, (float, SE, CountSym)):
                    return KVal(op == "~=")
                self.warn("count compare " + op)
                return KVal(op == "~=")
            if op in ("<", "<=", ">", ">="):
                self.warn("count compare " + op)
                big_left = isinstance(a, CountSym)
                if op in ("<", "<="):
                    return KVal(not big_left)
                return KVal(big_left)
        if isinstance(a, (Registers, Env, Captures, GenForIter, PackTable)) or isinstance(b, (Registers, Env, Captures, GenForIter, PackTable)):
            if op == "==":
                return a is b or (isinstance(a, Special) and isinstance(b, Special) and a.sig() == b.sig())
            if op == "~=":
                return not (a is b or (isinstance(a, Special) and isinstance(b, Special) and a.sig() == b.sig()))
            raise LiftError("arith on special")
        if op in ("==", "~=") and (isinstance(a, SE) or isinstance(b, SE)):
            other = b if isinstance(a, SE) else a
            if isinstance(other, (LuaFunction, Builtin, Registers, Env, Captures)) or (
                    isinstance(other, LuaTable) and not isinstance(other, ITable)):
                self.warn("opaque SE compare")
                return KVal(op == "~=")
        ea = self.to_expr(a)
        eb = self.to_expr(b)
        if ea[0] == "k" and eb[0] == "k":
            return KVal(super().binop(op, ea[1], eb[1]))
        return SE(("bin", op, ea, eb))

    def eq(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return self.binop("==", a, b)
        return super().eq(a, b)

    def lt(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return self.binop("<", a, b)
        return super().lt(a, b)

    def le(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            return self.binop("<=", a, b)
        return super().le(a, b)

    def concat(self, a, b):
        if isinstance(a, Sym) or isinstance(b, Sym):
            ua, ub = uw(a), uw(b)
            if not isinstance(ua, Sym) and not isinstance(ub, Sym):
                return KVal(super().concat(ua, ub))
            return SE(("bin", "..", self.to_expr(a), self.to_expr(b)))
        return super().concat(a, b)

    def length(self, v):
        if isinstance(v, KVal):
            return KVal(super().length(v.v))
        if isinstance(v, Sym):
            return SE(("un", "#", self.to_expr(v)))
        return super().length(v)

    def neg(self, v):
        if isinstance(v, KVal):
            return KVal(super().neg(v.v))
        if isinstance(v, Sym):
            return SE(("un", "-", self.to_expr(v)))
        return super().neg(v)

    def eval(self, e, sc):
        k = e.k
        if k == "Unop" and e.a[0] == "not":
            v = self.eval(e.a[1], sc)
            if isinstance(v, KVal):
                return KVal(not truthy(v.v))
            if isinstance(v, Sym) and not isinstance(v, NOTRUTH):
                if not (isinstance(v, SE) and v.e[0] in ("closure", "native", "newtable")):
                    return SE(("un", "not", self.to_expr(v)))
            return not self.test(v)
        if k in ("And", "Or"):
            a = self.eval(e.a[0], sc)
            if isinstance(a, Sym) and not isinstance(a, NOTRUTH) and not isinstance(a, KVal):
                n_ir = len(self.ctx.ir)
                b = self.eval(e.a[1], sc)
                if len(self.ctx.ir) == n_ir:
                    return SE(("and" if k == "And" else "or", self.to_expr(a), self.to_expr(b)))
                raise LiftError("side effects in symbolic and/or")
            if k == "And":
                if not self.test(a):
                    return a
                return self.eval(e.a[1], sc)
            if self.test(a):
                return a
            return self.eval(e.a[1], sc)
        if k in ("Call", "Method", "Vararg"):
            r = self.eval_multi(e, sc)
            return r[0] if r else None
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
        if isinstance(k, KVal):
            k = k.v
        if isinstance(t, ITable) and id(t) in self.closure_slots and self.closure_slots[id(t)][0] is t and not isinstance(k, Sym):
            self.closure_slots[id(t)][1][key_norm(k)] = self.ref_of(v)
        if isinstance(k, Sym):
            if isinstance(k, SE) and k.e[0] == "k":
                k = k.e[1]
            else:
                raise LiftError("rawset symbolic key")
        return super().rawset(t, k, v)

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
            o = self.eval(t.a[0], sc)
            key = self.eval(t.a[1], sc)
            cur = self.index(o, key)
            self.setindex(o, key, self.binop(op, cur, self.eval(rhs, sc)))
            return None
        if k == "NumFor":
            var, e1, e2, e3, body = s.a
            a = uw(self.eval(e1, sc))
            b = uw(self.eval(e2, sc))
            st = uw(self.eval(e3, sc)) if e3 is not None else 1.0
            if isinstance(a, Sym) or isinstance(b, Sym) or isinstance(st, Sym):
                return self.sym_loop(var, a, b, st, body, sc)
            a, b, st = tonum(a), tonum(b), tonum(st)
            i = a
            n = 0
            while (st > 0 and i <= b) or (st <= 0 and i >= b):
                n += 1
                if n > 100000:
                    raise LiftError("runaway loop")
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
            if vals and isinstance(vals[0], Sym):
                raise LiftError("generic for over symbolic value inside handler")
            return super().exec_stmt(s, sc)
        if k == "While":
            cond, body = s.a
            n = 0
            while self.test(self.eval(cond, sc)):
                n += 1
                if n > 100000:
                    raise LiftError("runaway while")
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
            n = 0
            while True:
                n += 1
                if n > 100000:
                    raise LiftError("runaway repeat")
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
        return super().exec_stmt(s, sc)

    def find_regs(self):
        for v in dict.values(self.fscope.vars):
            if isinstance(v, Registers):
                return v
        return None

    def sym_loop(self, var, a, b, st, body, sc):
        if not (isinstance(a, float) and st == 1.0):
            raise LiftError("symbolic loop start/step %r %r" % (a, st))
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
            adj = b.off - a + 1
            if len(regs) == 1 and regs[0][0] == "set" and regs[0][1][0] == "rL" and regs[0][2][0] in ("selL", "sel", "p"):
                dst_off = regs[0][1][1]
                val = regs[0][2]
                if val[0] == "p":
                    src, src_first = ("va",), val[1]
                else:
                    src = val[1]
                    src_first = val[2]
                if src != b.src:
                    raise LiftError("multret copy source mismatch")
                if src == ("va",):
                    if adj != -(src_first - 1):
                        raise LiftError("unexpected vararg copy shape %r %r" % (src_first, adj))
                    msrc = ("va", int(src_first))
                else:
                    if src_first != 1.0 or adj != 0:
                        raise LiftError("unexpected multret copy shape %r %r" % (src_first, adj))
                    msrc = src
                self.emit(("multicopy", int(dst_off), msrc))
                regs_obj = self.find_regs()
                if regs_obj is not None:
                    for i in list(regs_obj.known):
                        if i >= dst_off:
                            del regs_obj.known[i]
                    regs_obj.multi = (int(dst_off), msrc)
                return None
        raise LiftError("unrecognized symbolic loop: %r bounds %r %r" % (stmts, a, b))

    def eval_multi(self, e, sc):
        if e.k == "Vararg":
            return list(sc.varargs)
        return super().eval_multi(e, sc)

    # ---- setup
    def model(self, fn, name):
        b = Builtin(fn, name)
        self.model_ids.add(id(b))
        return b

    def install_models(self, state, runtime, native_tbl):
        self.vmstate = state
        vm = state.h
        self.real_factory = vm[56.0]

        def reg_val(regs, r):
            if r in regs.known:
                return KVal(regs.known[r])
            return SE(("r", r))

        def unpack_model(tbl, i, j):
            i, j = uw(i), uw(j)
            if isinstance(tbl, Registers):
                if isinstance(j, CountSym):
                    if tbl.multi is None:
                        raise LiftError("unpack to top without multi info")
                    base, msrc = tbl.multi
                    if msrc[0] == "va":
                        if j.src != ("va",) or j.off != base - msrc[1]:
                            raise LiftError("vararg top mismatch %r %r %r" % (j.src, j.off, tbl.multi))
                    else:
                        if j.src != msrc or j.off != base - 1:
                            raise LiftError("multret top mismatch %r %r %r" % (j.src, j.off, tbl.multi))
                    out = [reg_val(tbl, int(r)) for r in range(int(i), int(base))]
                    return out + [MultiSym(msrc)]
                if not isinstance(i, float) or not isinstance(j, float):
                    raise LiftError("unpack bounds symbolic %r %r" % (i, j))
                return [reg_val(tbl, int(r)) for r in range(int(i), int(j) + 1)]
            if isinstance(tbl, PackTable):
                if isinstance(j, CountSym) and j.off == 0 and i == 1.0:
                    return [MultiSym(tbl.src)]
                raise LiftError("unpack packtable %r %r" % (i, j))
            if isinstance(tbl, KVal):
                tbl = tbl.v
            if isinstance(tbl, LuaTable):
                ii = int(i) if i is not None else 1
                jj = int(j) if j is not None else tbl.length()
                return [tbl.h.get(float(k)) for k in range(ii, jj + 1)]
            raise LiftError("unpack of %r" % (tbl,))

        vm[27.0] = self.model(lambda a: unpack_model(a[2], a[0] if a[0] is not None else 1.0, a[1]), "unpack27")
        vm[26.0] = self.model(lambda a: unpack_model(a[2], a[1], a[0]), "unpack26")
        vm[4.0] = self.model(lambda a: unpack_model(a[0], a[1] if len(a) > 1 else 1.0, a[2] if len(a) > 2 else None), "unpack")

        def pack_model(a):
            if a and isinstance(a[-1], MultiSym):
                if len(a) == 1:
                    return [CountSym(a[0].src), PackTable(a[0].src)]
                raise LiftError("pack with prefix + multi")
            t = ITable({float(i + 1): v for i, v in enumerate(a)})
            return [float(len(a)), t]
        vm[55.0] = self.model(pack_model, "pack55")

        def select_model(a):
            if uw(a[0]) == b"#":
                if len(a) == 2 and isinstance(a[1], MultiSym):
                    return [CountSym(a[1].src)]
                if any(isinstance(x, MultiSym) for x in a[1:]):
                    raise LiftError("select # complex")
                return [float(len(a) - 1)]
            n = uw(a[0])
            if isinstance(n, float):
                rest = a[1:]
                if len(rest) == 1 and isinstance(rest[0], MultiSym):
                    if rest[0].src == ("va",):
                        raise LiftError("select(n, ...) on varargs")
                return rest[int(n) - 1:]
            raise LiftError("select model")
        vm[29.0] = self.model(select_model, "select")

        def newregs(a):
            if self.mode == "entry" and not self.ctx.regs_made:
                self.ctx.regs_made = True
                r = Registers()
                n = int(uw(a[0])) if a and isinstance(uw(a[0]), float) else 0
                for i in range(0, n + 2):
                    r.known[i] = None
                return [r]
            t = self.newtmp()
            self.emit(("set", t, ("newtable", (), ())))
            return [SE(t)]
        vm[10.0] = self.model(newregs, "table.create")
        vm[30.0] = self.model(lambda a: [Env()], "getfenv")
        vm[31.0] = self.model(lambda a: [a[0]], "setfenv")

        def newtable_model(a):
            t = self.newtmp()
            self.emit(("set", t, ("newtable", (), ())))
            return [SE(t)]
        vm[12.0] = self.model(newtable_model, "table.create")

        def move_model(a):
            src, f, e, t = a[0], uw(a[1]), uw(a[2]), uw(a[3])
            dst = a[4] if len(a) > 4 else src
            if isinstance(src, Registers):
                vals = unpack_model(src, f, e)
                self.emit(("setlist", self.to_expr(dst), self.to_expr(t), self.args_exprs(vals)))
                return [dst]
            raise LiftError("table.move model on %r" % (src,))
        vm[11.0] = self.model(move_model, "table.move")

        def wrap_model(a):
            fn = a[0]
            nv = 2
            for st in fn.node.a[2]:
                if st.k == "GenFor":
                    nv = len(st.a[0])
            pc = self.cur_pc()
            return [GenForIter(("fg", self.protoid, pc), nv)]
        vm[1.0] = self.model(wrap_model, "coroutine.wrap")

        def closure_model(a):
            proto, D = a[0], a[1]
            pid = self.proto_index.get(id(proto))
            if pid is None:
                raise LiftError("closure of unknown proto")
            self.track_slots(D)
            return [ClosureSym("closure", pid, D)]
        vm[56.0] = self.model(closure_model, "newclosure")
        names = {}
        for k, v in native_tbl.h.items():
            names[id(v)] = k.decode()
        for k, v in list(runtime.h.items()):
            if id(v) in names:
                nm = names[id(v)]
                runtime.h[k] = self.model((lambda nm: (lambda a: [self.track_slots(a[0]) or ClosureSym("native", nm, a[0])]))(nm), "native:" + nm)

    def resolve_ir(self, ir):
        """replace LazyRefs objects inside IR statements by resolved upvalue reference tuples"""
        for lz in self.lazies:
            if lz.resolved is None:
                lz.resolved = self.upval_refs(lz.D)
        self.lazies = []

        def fix(x):
            if isinstance(x, LazyRefs):
                return x.resolved if x.resolved is not None else self.upval_refs(x.D)
            if isinstance(x, tuple):
                return tuple(fix(y) for y in x)
            return x
        return [(d, fix(st)) for d, st in ir]

    def track_slots(self, D):
        if isinstance(D, KVal):
            D = D.v
        if isinstance(D, ITable):
            slots = {}
            for k, v in D.h.items():
                slots[k] = self.ref_of(v)
            self.closure_slots[id(D)] = (D, slots)
        return None

    def ref_of(self, v):
        if isinstance(v, ITable) and isinstance(v.h.get(3.0), Registers):
            L = int(uw(v.h[2.0]))
            regs = self.find_regs() or v.h[3.0]
            regs.captured.add(L)
            regs.known.pop(L, None)
            return ("rref", L)
        if isinstance(v, UpvalRef):
            return ("u", v.i)
        if v is None:
            return ("k", None)
        return ("val", self.to_expr(v))

    def upval_refs(self, D):
        if isinstance(D, KVal):
            D = D.v
        ent = self.closure_slots.get(id(D)) if D is not None else None
        if ent is not None and ent[0] is D:
            slots = ent[1]
            maxk = max([int(k) for k in slots if isinstance(k, float)], default=-1)
            return tuple(slots.get(float(i), ("k", None)) for i in range(0, maxk + 1))
        refs = []
        if D is None or D is False:
            return ()
        if isinstance(D, KVal):
            D = D.v
        if not isinstance(D, LuaTable):
            raise LiftError("upvalue list not a table")
        maxk = max([int(k) for k in D.h if isinstance(k, float)], default=-1)
        for i in range(0, maxk + 1):
            v = D.h.get(float(i))
            if isinstance(v, ITable) and isinstance(v.h.get(3.0), Registers):
                L = int(uw(v.h[2.0]))
                regs = self.find_regs() or v.h[3.0]
                regs.captured.add(L)
                regs.known.pop(L, None)
                refs.append(("rref", L))
            elif isinstance(v, UpvalRef):
                refs.append(("u", v.i))
            elif v is None:
                refs.append(("k", None))
            else:
                refs.append(("val", self.to_expr(v)))
        return tuple(refs)

    def cur_pc(self):
        return dict.get(self.fscope.vars, "pc")


# ---------------------------------------------------------------- state utilities
def sig2(v, _seen=None):
    """signature for control-state matching (data values are wildcards)"""
    if v is None or v is True or v is False:
        return ("c", v)
    if isinstance(v, (float, bytes)):
        return ("c", v)
    if isinstance(v, (KVal, SE)):
        return ("D",)
    if isinstance(v, Registers):
        return ("R",)
    if isinstance(v, ITable):
        return ("T",)
    if isinstance(v, Special):
        return v.sig()
    return ("o", id(v))


def compatible(a, b):
    if a == b:
        return True
    tn = (("T",), ("c", None))
    return a in tn and b in tn


def snapshot(vars_):
    memo = {}

    def cp(v):
        if isinstance(v, ITable):
            if id(v) in memo:
                return memo[id(v)]
            t = ITable()
            memo[id(v)] = t
            t.escaped = v.escaped
            t.closed = v.closed
            t.meta = v.meta
            for k2, v2 in v.h.items():
                t.h[k2] = cp(v2)
            return t
        if isinstance(v, Registers):
            if id(v) in memo:
                return memo[id(v)]
            r = v.copy()
            memo[id(v)] = r
            return r
        return v
    return {k: cp(v) for k, v in dict.items(vars_)}


def meet_states(A, B, pc, prefer=frozenset()):
    """returns (new_state, changed). raises Conflict on structural mismatch."""
    memo = {}
    changed = [False]

    def mv(a, b, path):
        if isinstance(a, (KVal, SE)) or isinstance(b, (KVal, SE)):
            if not (isinstance(a, (KVal, SE)) and isinstance(b, (KVal, SE))):
                return a
            if isinstance(a, KVal) and isinstance(b, KVal) and same(a.v, b.v):
                return a
            if isinstance(a, SE) and isinstance(b, SE) and a.e == b.e:
                return a
            gen = SE(("v", int(pc), path))
            if not (isinstance(a, SE) and a.e == gen.e):
                changed[0] = True
            return gen
        if isinstance(a, Registers) and isinstance(b, Registers):
            if id(a) in memo:
                return memo[id(a)]
            r = Registers()
            r.captured = a.captured | b.captured
            r.known = {i: v for i, v in a.known.items() if i in b.known and same(b.known[i], v) and i not in r.captured}
            r.multi = a.multi if a.multi == b.multi else None
            if r.captured != a.captured or len(r.known) != len(a.known) or r.multi != a.multi:
                changed[0] = True
            memo[id(a)] = r
            return r
        if path.count("/") > 6:
            return a
        if isinstance(a, ITable) and b is None:
            return a
        if a is None and isinstance(b, ITable):
            changed[0] = True
            return b
        if isinstance(a, ITable) and isinstance(b, ITable):
            if id(a) in memo:
                return memo[id(a)]
            if set(a.h) != set(b.h):
                if set(b.h) - set(a.h):
                    t = ITable()
                    t.escaped = a.escaped
                    t.closed = a.closed
                    memo[id(a)] = t
                    for k in set(a.h) | set(b.h):
                        if k in a.h and k in b.h:
                            t.h[k] = mv(a.h[k], b.h[k], path + "/" + fmtkey(k))
                        else:
                            t.h[k] = a.h[k] if k in a.h else b.h[k]
                    changed[0] = True
                    return t
                return a
            t = ITable()
            t.escaped = a.escaped
            t.closed = a.closed
            memo[id(a)] = t
            for k in a.h:
                t.h[k] = mv(a.h[k], b.h[k], path + "/" + fmtkey(k))
            return t
        return a

    out = {}
    for k in list(A):
        if k in prefer and A.get(k) is None and B.get(k) is not None:
            out[k] = B.get(k)
            changed[0] = True
            continue
        out[k] = mv(A.get(k), B.get(k), k)
    for k in prefer:
        if k not in A and k in B:
            out[k] = B[k]
            changed[0] = True
    return out, changed[0]


def fmtkey(k):
    if isinstance(k, float) and k.is_integer():
        return str(int(k))
    return repr(k)


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
        self.table_by_id = {id(t): t for t in tables.values()}
        I.install_models(state, runtime, Native)
        self.state = state
        self.Native = Native
        self.runtime = runtime
        I.warnings.clear()

    def is_resolver_slot(self, res, key):
        """True if states may be merged regardless of this patched slot (patched value preferred):
        - operand patches that do not act as jump targets (fake static operands fixed before use)
        - jump slots whose original target is resolver code"""
        tid, kk = key[1], key[2]
        t = self.table_by_id.get(tid)
        if t is None:
            return False
        pid = self.I.protoid
        ops = self.tables.get(self.F[pid][3].i) if pid in self.F else None
        if t is ops:
            return True
        orig = t.h.get(kk)
        blocks = res["blocks"]
        written = set(v for n, k2, v in self.I.static_writes if k2 == kk and isinstance(v, float))
        cand = set()
        if isinstance(orig, float):
            cand.add(orig + 1)
        for v in written:
            cand.add(v + 1)
        succ_pcs = set()
        for b in blocks.values():
            if b["pc"] != kk:
                continue
            for p in b["paths"]:
                if p[3][0] == "goto" and p[3][1] in blocks:
                    succ_pcs.add(blocks[p[3][1]]["pc"])
        if not (succ_pcs & cand) or succ_pcs <= {kk + 1}:
            return True   # not used as a jump target here
        if not isinstance(orig, float):
            return False
        target = orig + 1
        found = False
        for bid0, b0 in blocks.items():
            if b0["pc"] != target:
                continue
            found = True
            # follow chains of pure jumps (no real IR) to the resolver body
            bid = bid0
            for step in range(25):
                b = blocks[bid]
                real = []
                for p in b["paths"]:
                    for d, st in p[2]:
                        if st[0] == "set" and st[2][0] == "static":
                            return True
                        if not (st[0] == "set" and st[1][0] == "v"):
                            real.append(st)
                if real or len(b["paths"]) != 1 or b["paths"][0][3][0] != "goto":
                    break
                bid = b["paths"][0][3][1]
                if bid not in blocks:
                    break
        return not found

    def lift(self, pid, verbose=False, max_retries=16):
        extra = collections.defaultdict(set)
        extra["__global__"] = set()
        extra["__dontcare__"] = set()
        self.I.static_writes = []
        for attempt in range(max_retries):
            res = lift_once(self, pid, extra, verbose)
            if not res["conflicts"]:
                return res
            for pc, v in res["conflicts"]:
                if isinstance(v, tuple) and v and v[0] == "mem":
                    if self.is_resolver_slot(res, v):
                        extra["__dontcare__"].add(v)
                    else:
                        extra["__global__"].add(v)
                        if verbose:
                            t = self.table_by_id.get(v[1])
                            print("  global split key", t.name if t else v[1], v[2], "orig", t.h.get(v[2]) if t else None, flush=True)
                else:
                    extra[pc].add(v)
            if verbose:
                print("  dontcare", len(extra["__dontcare__"]), "global", len(extra["__global__"]), flush=True)
            if verbose:
                print("retry", attempt, sorted(res["conflicts"], key=repr)[:10])
        raise LiftError("could not stabilise block keys for proto %d" % pid)


def is_dispatch_loop(s):
    lb = s.a[0] if s.k == "Repeat" else s.a[1]
    for st in lb[:2]:
        if st.k == "Local" and st.a[0] == ["opcode"]:
            return True
    return False


def find_main_loop(body):
    for s in body:
        if s.k in ("While", "Repeat") and is_dispatch_loop(s):
            return s
        if s.k == "If":
            for sub in (s.a[1], s.a[2] or []):
                r = find_main_loop(sub)
                if r is not None:
                    return r
    for s in body:
        if s.k in ("While", "Repeat"):
            return s
    return None


def lift_once(L, pid, extra, verbose=False):
    I = L.I
    proto = L.tables[pid]
    I.protoid = pid
    I.blockid = 0
    I.ctx = PathCtx([])
    I.fscope = Scope(None)
    I.mode = None
    f = I.call(I.real_factory, [proto, Captures()])[0]
    if not isinstance(f, LuaFunction):
        raise LiftError("factory did not return a lua function")
    params, vararg, body = f.node.a
    main = find_main_loop(body)
    I.main_loop_node = main
    blocks = {}
    variants = collections.defaultdict(list)
    reads_at = collections.defaultdict(set)
    inc = collections.defaultdict(dict)
    outs = collections.defaultdict(set)
    deps = collections.defaultdict(set)
    queue = collections.deque()
    queued = set()
    nproc = [0]

    def schedule(bid):
        if bid not in queued:
            queued.add(bid)
            queue.append(bid)

    writers = collections.defaultdict(set)

    def on_write(t, kk, kind):
        key = (id(t), kk)
        if kind == "write":
            writers[key].add(I.blockid)
            return
        for b in deps.get(key, ()):
            schedule(b)
        for b in writers.get(key, ()):
            schedule(b)
    I.on_static_write = on_write

    def make_scope():
        sc = Scope(f.scope)
        sc.vars = TrackDict()
        sc.varargs = [MultiSym(("va",))]
        return sc

    def spill(vars_, pc):
        seen = set()

        def visit(container, key, v, path, is_dict):
            if isinstance(v, SE) and v.e[0] != "k":
                name = ("v", int(pc), path)
                if v.e != name:
                    I.emit(("set", name, v.e))
                if is_dict:
                    dict.__setitem__(container, key, SE(name))
                else:
                    container[key] = SE(name)
            elif isinstance(v, KVal):
                I.emit(("set", ("v", int(pc), path), I.to_expr(v)))
            elif isinstance(v, SE):
                I.emit(("set", ("v", int(pc), path), v.e))
            elif isinstance(v, ITable) and id(v) not in seen:
                seen.add(id(v))
                for k2 in list(v.h):
                    visit(v.h, k2, v.h[k2], path + "/" + fmtkey(k2), False)
        for k in list(dict.keys(vars_)):
            visit(vars_, k, dict.__getitem__(vars_, k), k, True)

    def run_paths(start_vars, mode, bid):
        I.blockid = bid
        stack = [[]]
        paths = []
        livein = set()
        reads = set()
        while stack:
            dec = stack.pop()
            ctx = PathCtx(dec)
            I.ctx = ctx
            I.cur_reads = reads
            I.closure_slots = {}
            I.lazies = []
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
                outcome = ("lifterr", str(e) + " @line " + str(getattr(e, "line", None)))
            except Tamper as e:
                outcome = ("tamper", str(e))
            except RecursionError:
                outcome = ("lifterr", "recursion")
            while I.journal:
                tbl, kk, old = I.journal.pop()
                if old is None:
                    tbl.h.pop(kk, None)
                else:
                    tbl.h[kk] = old
            livein |= sc.vars.reads
            writes = set(sc.vars.writes)
            if outcome[0] == "head":
                pc = dict.get(sc.vars, "pc")
                spill(sc.vars, pc)
                outcome = ("head", dict(sc.vars))
            elif outcome[0] == "ret":
                try:
                    outcome = ("ret", I.args_exprs(outcome[1]))
                except LiftError as e:
                    outcome = ("lifterr", str(e))
            try:
                ctx.ir = I.resolve_ir(ctx.ir)
                if outcome[0] == "ret":
                    outcome = ("ret", I.resolve_ir([(0, outcome[1])])[0][1])
            except LiftError as e:
                outcome = ("lifterr", "lazy refs: " + str(e))
            paths.append([ctx.taken, ctx.conds, ctx.ir, outcome, writes])
            for alt in ctx.alts:
                stack.append(alt)
            if len(paths) > 5000:
                raise LiftError("path explosion in block %d" % bid)
        I.cur_reads = None
        return paths, livein, reads

    gkeys = extra.get("__global__", set())
    dontcare = extra.get("__dontcare__", set())

    def matches(bid, st):
        pc = st.get("pc")
        rep = blocks[bid]["state"]
        if pc in reads_at:
            names = (reads_at[pc] | extra[pc] | gkeys) - dontcare
        else:
            names = (set(rep) | set(st) | gkeys) - dontcare
        for n in names:
            if not compatible(sig2(st.get(n)), sig2(rep.get(n))):
                return False
        return True

    def find_or_add(st, src):
        pc = st.get("pc")
        for bid in variants[pc]:
            if matches(bid, st):
                try:
                    new, changed = meet_states(blocks[bid]["state"], st, pc, dontcare)
                except Conflict:
                    continue
                inc[bid][src] = st
                if changed:
                    if DEBUG_MEET:
                        old_st = blocks[bid]["state"]
                        cnt = DEBUG_MEET.setdefault(bid, 0) + 1
                        DEBUG_MEET[bid] = cnt
                        if cnt in (30, 31, 32):
                            diffs = []
                            for kk in set(old_st) | set(new):
                                a1, b1 = old_st.get(kk), new.get(kk)
                                if repr(a1) != repr(b1):
                                    diffs.append((kk, repr(a1)[:80], repr(b1)[:80]))
                            ra, rb = old_st.get("registers"), new.get("registers")
                            if isinstance(ra, Registers) and isinstance(rb, Registers):
                                diffs.append(("known", sorted(set(ra.known) ^ set(rb.known))[:10], (ra.captured ^ rb.captured), (ra.multi, rb.multi)))
                            def dumpt(t, d=0):
                                if isinstance(t, ITable):
                                    if d > 4:
                                        return "T..."
                                    return "{" + ",".join("%s:%s" % (fmtkey(k), dumpt(v, d + 1)) for k, v in sorted(t.h.items(), key=lambda x: repr(x[0]))) + "}"
                                return repr(t)[:60]
                            print("MEET CHANGE", bid, pc, "OLD X", dumpt(old_st.get("X")), "NEW X", dumpt(new.get("X")), "INC X", dumpt(st.get("X")), flush=True)
                    blocks[bid]["state"] = new
                    schedule(bid)
                return bid
        bid = len(blocks)
        blocks[bid] = {"pc": pc, "state": st, "paths": None, "reads": set()}
        variants[pc].append(bid)
        inc[bid][src] = st
        schedule(bid)
        return bid

    def process(bid):
        import time as _time
        _t0 = _time.time()
        blk = blocks[bid]
        for t in outs[bid]:
            for key in [k for k in inc[t] if k[0] == bid]:
                del inc[t][key]
        outs[bid] = set()
        mode = "entry" if bid == 0 else "loop"
        paths, livein, reads = run_paths(blk["state"], mode, bid)
        for key in reads:
            deps[key].add(bid)
        blk["paths"] = paths
        blk["reads"] = livein
        _dt = _time.time() - _t0
        if verbose and _dt > 5:
            print("   slow block", bid, "pc", blk["pc"], "%.1fs" % _dt, "paths", len(paths), flush=True)
        if blk["pc"] is not None:
            reads_at[blk["pc"]] |= livein
        for i, p in enumerate(paths):
            if p[3][0] == "head":
                st = p[3][1]
                t = find_or_add(st, (bid, i))
                p[3] = ("goto", t)
                p.append(st)
                outs[bid].add(t)

    blocks[0] = {"pc": None, "state": None, "paths": None, "reads": set()}
    schedule(0)
    while queue:
        bid = queue.pop()
        queued.discard(bid)
        process(bid)
        nproc[0] += 1
        if verbose and nproc[0] % 2000 == 0:
            print("  processed", nproc[0], "blocks", len(blocks), "queue", len(queue), "static writes", len(I.static_writes), flush=True)
        if nproc[0] > 200000:
            raise LiftError("no convergence")
    I.on_static_write = None
    # reachability
    reach = set()
    stack = [0]
    while stack:
        b = stack.pop()
        if b in reach:
            continue
        reach.add(b)
        for p in blocks[b]["paths"]:
            if p[3][0] == "goto":
                stack.append(p[3][1])
    # liveness of control locals (worklist)
    live = {b: set(blocks[b]["reads"]) for b in reach}
    preds = collections.defaultdict(set)
    for b in reach:
        for p in blocks[b]["paths"]:
            if p[3][0] == "goto":
                preds[p[3][1]].add(b)
    work = collections.deque(reach)
    inwork = set(reach)
    while work:
        b = work.popleft()
        inwork.discard(b)
        cur = live[b]
        grew = False
        for p in blocks[b]["paths"]:
            if p[3][0] == "goto":
                add = live[p[3][1]] - p[4]
                if add and not add <= cur:
                    cur |= add
                    grew = True
        if grew:
            for pb in preds[b]:
                if pb not in inwork:
                    inwork.add(pb)
                    work.append(pb)
    conflicts = set()
    for b in reach:
        if b == 0:
            continue
        rep = blocks[b]["state"]
        for (pb, pi), st in inc[b].items():
            if pb not in reach:
                continue
            for v in live[b]:
                if v in dontcare:
                    continue
                if not compatible(sig2(st.get(v)), sig2(rep.get(v))):
                    conflicts.add((blocks[b]["pc"], v))
    final = {b: blocks[b] for b in reach}
    return {"blocks": final, "conflicts": conflicts, "nproc": nproc[0]}
