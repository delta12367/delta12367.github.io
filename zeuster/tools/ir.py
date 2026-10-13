"""IR utilities, CFG construction and optimisation for lifted Luraph prototypes."""
import collections

VAR_KINDS = ("r", "t", "v", "x", "s", "w")      # x = decompiler-introduced temp
MEM_KINDS = ("u", "g", "idx")


def is_var(e):
    return e[0] in VAR_KINDS


# ----------------------------------------------------------------------------- expression walking
def children(e):
    k = e[0]
    if k in ("k", "r", "t", "v", "x", "u", "p", "builtin", "static", "hostfn", "count", "fgok", "fgv", "sel",
             "multi", "rL", "selL", "regfile", "xm", "w", "s", "flagvar", "spill"):
        if k == "count" or k == "sel" or k == "multi":
            return ()
        return ()
    if k == "g":
        return (e[1],)
    if k == "idx":
        return (e[1], e[2])
    if k == "bin":
        return (e[2], e[3])
    if k == "un":
        return (e[2],)
    if k in ("and", "or"):
        return (e[1], e[2])
    if k == "newtable":
        out = list(e[1])
        for a, b in e[2]:
            out.append(a)
            out.append(b)
        return tuple(out)
    if k in ("closure", "native"):
        out = []
        for r in e[2]:
            if r[0] == "val":
                out.append(r[1])
            elif r[0] == "rref":
                out.append(("r", r[1]))
            elif r[0] == "rrefv":
                out.append(r[1])
        return tuple(out)
    if k in ("callx", "callmx"):
        return (e[1],) + tuple(e[2])
    if k in ("mcallx", "mcallmx"):
        return (e[1],) + tuple(e[3])
    if k == "vararg":
        return ()
    raise ValueError("unknown expr " + repr(k))


def rebuild(e, ch):
    k = e[0]
    if k == "g":
        return ("g", ch[0])
    if k == "idx":
        return ("idx", ch[0], ch[1])
    if k == "bin":
        return ("bin", e[1], ch[0], ch[1])
    if k == "un":
        return ("un", e[1], ch[0])
    if k in ("and", "or"):
        return (k, ch[0], ch[1])
    if k == "newtable":
        n = len(e[1])
        arr = tuple(ch[:n])
        hs = []
        for i in range(len(e[2])):
            hs.append((ch[n + 2 * i], ch[n + 2 * i + 1]))
        return ("newtable", arr, tuple(hs))
    if k in ("closure", "native"):
        refs = []
        i = 0
        for r in e[2]:
            if r[0] == "val":
                refs.append(("val", ch[i]))
                i += 1
            elif r[0] in ("rref", "rrefv"):
                c = ch[i]
                i += 1
                if c[0] == "r":
                    refs.append(("rref", c[1]))
                else:
                    refs.append(("rrefv", c))
            else:
                refs.append(r)
        return (k, e[1], tuple(refs))
    if k in ("callx", "callmx"):
        return (k, ch[0], tuple(ch[1:]))
    if k in ("mcallx", "mcallmx"):
        return (k, ch[0], e[2], tuple(ch[1:]))
    return e


def subst(e, fn):
    """bottom-up substitution: fn(e) returns replacement or None"""
    r = fn(e)
    if r is not None:
        return r
    ch = children(e)
    if not ch:
        return e
    nch = tuple(subst(c, fn) for c in ch)
    if nch == ch:
        return e
    return rebuild(e, nch)


def walk(e):
    yield e
    for c in children(e):
        yield from walk(c)


def vars_read(e, out=None):
    if out is None:
        out = set()
    for x in walk(e):
        if x[0] in VAR_KINDS:
            out.add(x)
    return out


def has_effects(e):
    for x in walk(e):
        if x[0] in ("callx", "callmx", "mcallx", "mcallmx"):
            return True
    return False


def reads_mem(e):
    for x in walk(e):
        if x[0] in MEM_KINDS or x[0] in ("callx", "callmx", "mcallx", "mcallmx", "sel", "fgv", "fgok", "count"):
            return True
    return False


def may_raise(e):
    """operators that can call a metamethod or fail on the wrong types (so their position relative to
    side effects is observable): arithmetic, concatenation, ordering comparisons, length, unary minus"""
    for x in walk(e):
        k = x[0]
        if k == "bin" and x[1] not in ("==", "~="):
            return True
        if k == "un" and x[1] in ("#", "-"):
            return True
    return False


def has_alloc(e):
    for x in walk(e):
        if x[0] in ("closure", "native", "newtable"):
            return True
    return False


def count_uses(e, var):
    n = 0
    for x in walk(e):
        if x == var:
            n += 1
    return n


# ----------------------------------------------------------------------------- statements
# set: ('set', target, expr)
# call: ('call', cid, f, args)  -> fused later
# callstat: ('callstat', callexpr)
# mset: ('mset', targets, callexpr)
# multicopy, setlist, fginit, fgnext

def stmt_exprs(s):
    """expressions evaluated by statement (reads)"""
    k = s[0]
    if k == "set":
        t = s[1]
        out = [s[2]]
        if t[0] == "idx":
            out = [t[1], t[2], s[2]]
        elif t[0] == "g":
            out = [t[1], s[2]]
        return out
    if k == "call":
        return [s[2]] + list(s[3])
    if k == "callstat":
        return [s[1]]
    if k == "mset":
        return [s[2]]
    if k == "setlist":
        return [s[1], s[2]] + list(s[3])
    if k == "fginit":
        return [s[2], s[3], s[4]]
    if k in ("fgnext", "multicopy", "mcopy", "close"):
        return []
    if k == "local":
        return []
    raise ValueError(k)


def stmt_with_exprs(s, ex):
    k = s[0]
    if k == "set":
        t = s[1]
        if t[0] == "idx":
            return ("set", ("idx", ex[0], ex[1]), ex[2])
        if t[0] == "g":
            return ("set", ("g", ex[0]), ex[1])
        return ("set", t, ex[0])
    if k == "call":
        return ("call", s[1], ex[0], tuple(ex[1:]))
    if k == "callstat":
        return ("callstat", ex[0])
    if k == "mset":
        return ("mset", s[1], ex[0])
    if k == "setlist":
        return ("setlist", ex[0], ex[1], tuple(ex[2:]))
    if k == "fginit":
        return ("fginit", s[1], ex[0], ex[1], ex[2])
    return s


def stmt_defs(s):
    """variables written"""
    k = s[0]
    if k == "set" and s[1][0] in VAR_KINDS:
        return [s[1]]
    if k == "mset":
        return [t for t in s[1] if t[0] in VAR_KINDS]
    return []


def stmt_effects(s):
    """does the statement have side effects beyond writing variables (memory writes / calls)"""
    k = s[0]
    if k == "set":
        if s[1][0] not in VAR_KINDS:
            return True
        return has_effects(s[2])
    if k in ("call", "callstat", "mset", "setlist", "fginit", "fgnext"):
        return True
    return False


def term_exprs(t):
    if t[0] == "br":
        return [t[1]]
    if t[0] == "ret":
        return list(t[1])
    return []


def term_with_exprs(t, ex):
    if t[0] == "br":
        return ("br", ex[0], t[2], t[3])
    if t[0] == "ret":
        return ("ret", tuple(ex))
    return t


# ----------------------------------------------------------------------------- CFG
class BB:
    __slots__ = ("id", "stmts", "term", "preds", "pcs")

    def __init__(self, id_):
        self.id = id_
        self.stmts = []
        self.term = None
        self.preds = set()
        self.pcs = []

    def succs(self):
        t = self.term
        if t[0] == "jmp":
            return [t[1]]
        if t[0] == "br":
            return [t[2], t[3]]
        return []

    def __repr__(self):
        return "BB%d" % self.id


class Func:
    def __init__(self):
        self.bbs = {}
        self.entry = None
        self.nextid = 0

    def new_bb(self):
        b = BB(self.nextid)
        self.nextid += 1
        self.bbs[b.id] = b
        return b

    def compute_preds(self):
        for b in self.bbs.values():
            b.preds = set()
        for b in self.bbs.values():
            for s in b.succs():
                self.bbs[s].preds.add(b.id)

    def reachable(self):
        seen = set()
        st = [self.entry]
        while st:
            x = st.pop()
            if x in seen:
                continue
            seen.add(x)
            st.extend(self.bbs[x].succs())
        return seen

    def prune(self):
        r = self.reachable()
        for k in list(self.bbs):
            if k not in r:
                del self.bbs[k]
        self.compute_preds()


def build_func(blocks):
    """blocks: bid -> {pc, paths:[(taken, conds, ir, outcome)]}"""
    F = Func()
    entry_of = {}
    pending = []

    def build_tree(bid, paths):
        def rec(ps, depth):
            first = ps[0]
            stmts = [st for d, st in first[2] if d == depth]
            bb = F.new_bb()
            bb.stmts = list(stmts)
            bb.pcs = [blocks[bid]["pc"]]
            if len(first[0]) == depth:
                out = first[3]
                if out[0] == "goto":
                    pending.append((bb, out[1]))
                    bb.term = ("jmp", None)
                elif out[0] == "ret":
                    bb.term = ("ret", tuple(out[1]))
                else:
                    bb.term = ("err", (out[0] + ": " + str(out[1])) if len(out) > 1 else str(out))
                return bb.id
            cond = first[1][depth]
            tps = [p for p in ps if p[0][depth] is True]
            fps = [p for p in ps if p[0][depth] is False]
            t = rec(tps, depth + 1) if tps else None
            f = rec(fps, depth + 1) if fps else None
            if t is None or f is None:
                bb.term = ("jmp", t if t is not None else f)
            else:
                bb.term = ("br", cond, t, f)
            return bb.id
        return rec(paths, 0)

    for bid in sorted(blocks):
        entry_of[bid] = build_tree(bid, blocks[bid]["paths"])
    for bb, target in pending:
        bb.term = ("jmp", entry_of[target])
    F.entry = entry_of[0]
    F.prune()
    simplify_cfg(F)
    return F


def simplify_cfg(F):
    changed = True
    while changed:
        changed = False
        F.compute_preds()
        # skip empty jump-only blocks
        for b in list(F.bbs.values()):
            if b.id not in F.bbs:
                continue
            t = b.term
            if t[0] == "jmp" and not b.stmts and b.id != F.entry and t[1] != b.id:
                tgt = t[1]
                for p in list(b.preds):
                    pb = F.bbs[p]
                    pb.term = retarget(pb.term, b.id, tgt)
                if b.preds:
                    changed = True
            # identical-branch conditional
            if t[0] == "br" and t[2] == t[3]:
                b.term = ("jmp", t[2])
                changed = True
        F.prune()
        # merge chains
        for b in list(F.bbs.values()):
            if b.id not in F.bbs:
                continue
            while b.term[0] == "jmp":
                nxt = F.bbs[b.term[1]]
                if nxt.id == F.entry or nxt.id == b.id or len(nxt.preds) != 1:
                    break
                b.stmts.extend(nxt.stmts)
                b.pcs.extend(nxt.pcs)
                b.term = nxt.term
                del F.bbs[nxt.id]
                for s in b.succs():
                    sb = F.bbs[s]
                    sb.preds.discard(nxt.id)
                    sb.preds.add(b.id)
                changed = True
    F.compute_preds()


def retarget(t, old, new):
    if t[0] == "jmp" and t[1] == old:
        return ("jmp", new)
    if t[0] == "br":
        a = new if t[2] == old else t[2]
        b = new if t[3] == old else t[3]
        return ("br", t[1], a, b)
    return t


# ----------------------------------------------------------------------------- liveness
def stmt_uses(s):
    out = set()
    for e in stmt_exprs(s):
        vars_read(e, out)
    return out


def liveness(F):
    use = {}
    defs = {}
    for b in F.bbs.values():
        u = set()
        d = set()
        for s in b.stmts:
            for v in stmt_uses(s):
                if v not in d:
                    u.add(v)
            for v in stmt_defs(s):
                d.add(v)
        for e in term_exprs(b.term):
            for v in vars_read(e):
                if v not in d:
                    u.add(v)
        use[b.id] = u
        defs[b.id] = d
    live_in = {b: set() for b in F.bbs}
    live_out = {b: set() for b in F.bbs}
    order = list(F.bbs)
    changed = True
    while changed:
        changed = False
        for bid in reversed(order):
            b = F.bbs[bid]
            out = set()
            for s in b.succs():
                out |= live_in[s]
            inn = use[bid] | (out - defs[bid])
            if out != live_out[bid] or inn != live_in[bid]:
                live_out[bid] = out
                live_in[bid] = inn
                changed = True
    return live_in, live_out
