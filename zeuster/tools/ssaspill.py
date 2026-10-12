"""SSA-based copy propagation for lifter spill variables ('v', pc, path).

Braun et al. on-the-fly SSA construction restricted to spill variables. Copies between spill variables become
aliases, trivial phis are removed, constant definitions are propagated, and the remaining values are coalesced
(phi webs) into fresh variables ('s', n).
"""
import collections
from ir import walk, subst, stmt_exprs, term_exprs, term_with_exprs, stmt_defs
from opt import stmt_exprs_any, map_stmt


def is_spill(e):
    return e[0] == "v"


class Phi:
    __slots__ = ("id", "block", "ops", "users", "var", "replaced")

    def __init__(self, id_, block, var):
        self.id = id_
        self.block = block
        self.ops = []
        self.users = set()
        self.var = var
        self.replaced = None


class SSA:
    def __init__(self, F, order):
        self.F = F
        self.order = order
        self.current = collections.defaultdict(dict)   # var -> block -> value
        self.sealed = set()
        self.incomplete = collections.defaultdict(dict)
        self.phis = {}
        self.nextid = 0
        self.defval = {}        # value id -> ('def', bb, idx) | ('const', expr) | ('entry', var)
        self.alias = {}

    def new_value(self, info):
        self.nextid += 1
        v = "d%d" % self.nextid
        self.defval[v] = info
        return v

    def resolve(self, v):
        while True:
            if v in self.alias:
                v = self.alias[v]
                continue
            if v in self.phis and self.phis[v].replaced is not None:
                v = self.phis[v].replaced
                continue
            return v

    def write(self, var, block, value):
        self.current[var][block] = value

    def read(self, var, block):
        cur = self.current[var]
        if block in cur:
            return self.resolve(cur[block])
        return self.read_rec(var, block)

    def read_rec(self, var, block):
        preds = self.F.bbs[block].preds
        if block not in self.sealed:
            phi = self.new_phi(block, var)
            self.incomplete[block][var] = phi
            val = phi.id
        elif len(preds) == 1:
            val = self.read(var, next(iter(preds)))
        elif len(preds) == 0:
            val = self.new_value(("entry", var))
        else:
            phi = self.new_phi(block, var)
            self.write(var, block, phi.id)
            val = self.add_ops(phi)
        self.write(var, block, val)
        return val

    def new_phi(self, block, var):
        self.nextid += 1
        p = Phi("p%d" % self.nextid, block, var)
        self.phis[p.id] = p
        return p

    def add_ops(self, phi):
        for pred in self.F.bbs[phi.block].preds:
            v = self.read(phi.var, pred)
            phi.ops.append(v)
            if v in self.phis:
                self.phis[v].users.add(phi.id)
        return self.try_remove_trivial(phi)

    def try_remove_trivial(self, phi):
        same = None
        for op in phi.ops:
            op = self.resolve(op)
            if op == same or op == phi.id:
                continue
            if same is not None:
                return phi.id
            same = op
        if same is None:
            same = self.new_value(("undef", phi.var))
        phi.replaced = same
        for u in list(phi.users):
            if u != phi.id and u in self.phis and self.phis[u].replaced is None:
                self.try_remove_trivial(self.phis[u])
        return same

    def seal(self, block):
        for var, phi in self.incomplete[block].items():
            self.add_ops(phi)
        self.incomplete[block] = {}
        self.sealed.add(block)


def run(F):
    """rewrite spill variables in F. returns number of copies removed"""
    from structure import rpo
    order = rpo(F)
    S = SSA(F, order)
    processed = set()
    use_val = {}      # (bb, idx, var) -> value  (idx may be 'term')
    def_val = {}      # (bb, idx) -> value for spill defs
    copy_defs = set()

    def try_seal_all():
        for b in order:
            if b in S.sealed:
                continue
            if all(p in processed for p in F.bbs[b].preds):
                S.seal(b)

    for b in order:
        bb = F.bbs[b]
        if b not in S.sealed and all(p in processed for p in bb.preds):
            S.seal(b)
        for idx, s in enumerate(bb.stmts):
            used = set()
            for e in stmt_exprs_any(s):
                for x in walk(e):
                    if is_spill(x):
                        used.add(x)
            for v in used:
                use_val[(b, idx, v)] = S.read(v, b)
            for d in stmt_defs(s):
                if is_spill(d):
                    rhs = s[2] if s[0] == "set" else None
                    if rhs is not None and is_spill(rhs):
                        val = use_val[(b, idx, rhs)]
                        copy_defs.add((b, idx))
                    elif rhs is not None and rhs[0] == "k":
                        val = S.new_value(("const", rhs))
                        def_val[(b, idx)] = val
                    else:
                        val = S.new_value(("def", b, idx))
                        def_val[(b, idx)] = val
                    S.write(d, b, val)
        used = set()
        for e in term_exprs(bb.term):
            for x in walk(e):
                if is_spill(x):
                    used.add(x)
        for v in used:
            use_val[(b, "term", v)] = S.read(v, b)
        processed.add(b)
        try_seal_all()
    for b in order:
        if b not in S.sealed:
            S.seal(b)
    # coalesce phis with their operands
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a
    for pid, phi in S.phis.items():
        if phi.replaced is not None:
            continue
        for op in phi.ops:
            union(pid, S.resolve(op))
    names = {}
    merged_roots = set()
    for pid, phi in S.phis.items():
        if phi.replaced is None:
            merged_roots.add(find(pid))

    def var_of(val):
        val = S.resolve(val)
        info = S.defval.get(val)
        r = find(val)
        if r not in names:
            names[r] = ("s", len(names) + 1)
        return names[r]

    def const_of(val):
        val = S.resolve(val)
        info = S.defval.get(val)
        if info and info[0] == "const":
            # only if not merged with other values through a phi
            if find(val) not in merged_roots:
                return info[1]
        if info and info[0] in ("undef", "entry"):
            if find(val) not in merged_roots:
                return ("k", None)
        return None
    removed = 0
    for b in order:
        bb = F.bbs[b]
        new = []
        for idx, s in enumerate(bb.stmts):
            if (b, idx) in copy_defs:
                removed += 1
                continue

            def fu(e, _b=b, _idx=idx):
                if is_spill(e):
                    val = use_val.get((_b, _idx, e))
                    if val is None:
                        return None
                    c = const_of(val)
                    if c is not None:
                        return c
                    return var_of(val)
                return None
            s2 = map_stmt(s, lambda e: subst(e, fu))
            if s2[0] == "set" and is_spill(s2[1]):
                val = def_val.get((b, idx))
                if val is not None:
                    if const_of(val) is not None:
                        removed += 1
                        continue
                    s2 = ("set", var_of(val), s2[2])
            new.append(s2)
        bb.stmts = new

        def ft(e, _b=b):
            if is_spill(e):
                val = use_val.get((_b, "term", e))
                if val is None:
                    return None
                c = const_of(val)
                if c is not None:
                    return c
                return var_of(val)
            return None
        bb.term = term_with_exprs(bb.term, [subst(e, ft) for e in term_exprs(bb.term)])
    return removed
