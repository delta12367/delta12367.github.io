"""Split variables into webs (def-use connected components) using reaching definitions, rename to ('w', id)."""
import collections
from ir import (VAR_KINDS, walk, subst, stmt_exprs, stmt_defs, term_exprs, term_with_exprs, vars_read)
from opt import stmt_exprs_any, map_stmt, captured_regs


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        p = self.p
        while p.setdefault(x, x) != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[b] = a


def is_v(e):
    return e[0] in VAR_KINDS


def compute_webs(F, nparams):
    cap = captured_regs(F)
    # def ids
    defs = []           # id -> var
    def_site = {}       # (bbid, idx, var) -> id
    var_defs = collections.defaultdict(list)
    order = list(F.bbs)
    # entry pseudo defs for every variable
    allvars = set()
    for b in F.bbs.values():
        for s in b.stmts:
            for e in stmt_exprs_any(s):
                allvars |= vars_read(e)
            for v in stmt_defs(s):
                allvars.add(v)
        for e in term_exprs(b.term):
            allvars |= vars_read(e)
    entry_def = {}
    for v in sorted(allvars, key=repr):
        i = len(defs)
        defs.append(v)
        entry_def[v] = i
        var_defs[v].append(i)
    for b in F.bbs.values():
        for idx, s in enumerate(b.stmts):
            for v in stmt_defs(s):
                i = len(defs)
                defs.append(v)
                def_site[(b.id, idx, v)] = i
                var_defs[v].append(i)
    mask = {}
    for v, ids in var_defs.items():
        m = 0
        for i in ids:
            m |= 1 << i
        mask[v] = m
    gen = {}
    kill = {}
    for b in F.bbs.values():
        g = 0
        k = 0
        for idx, s in enumerate(b.stmts):
            for v in stmt_defs(s):
                i = def_site[(b.id, idx, v)]
                g = (g & ~mask[v]) | (1 << i)
                k |= mask[v]
        gen[b.id] = g
        kill[b.id] = k
    IN = {b: 0 for b in F.bbs}
    OUT = {b: gen[b] for b in F.bbs}
    entry_bits = 0
    for v, i in entry_def.items():
        entry_bits |= 1 << i
    preds = {b: F.bbs[b].preds for b in F.bbs}
    changed = True
    while changed:
        changed = False
        for bid in order:
            inn = entry_bits if bid == F.entry else 0
            for p in preds[bid]:
                inn |= OUT[p]
            out = gen[bid] | (inn & ~kill[bid])
            if inn != IN[bid] or out != OUT[bid]:
                IN[bid] = inn
                OUT[bid] = out
                changed = True
    uf = UF()
    for i in range(len(defs)):
        uf.find(i)
    use_site = {}   # (bbid, idx or 'term', var) -> representative def id

    def bits(x):
        out = []
        while x:
            low = x & -x
            out.append(low.bit_length() - 1)
            x ^= low
        return out

    for b in F.bbs.values():
        cur = IN[b.id]
        for idx, s in enumerate(b.stmts):
            used = set()
            for e in stmt_exprs_any(s):
                used |= vars_read(e)
            for v in used:
                rd = bits(cur & mask[v])
                if not rd:
                    rd = [entry_def[v]]
                for d in rd[1:]:
                    uf.union(rd[0], d)
                use_site[(b.id, idx, v)] = rd[0]
            for v in stmt_defs(s):
                i = def_site[(b.id, idx, v)]
                cur = (cur & ~mask[v]) | (1 << i)
        used = set()
        for e in term_exprs(b.term):
            used |= vars_read(e)
        for v in used:
            rd = bits(cur & mask[v])
            if not rd:
                rd = [entry_def[v]]
            for d in rd[1:]:
                uf.union(rd[0], d)
            use_site[(b.id, "term", v)] = rd[0]
    # captured registers: a capture keeps the variable alive (and shared) until the VM closes the upvalue
    captures = []
    for b in F.bbs.values():
        for idx, s in enumerate(b.stmts):
            for e in stmt_exprs_any(s):
                for x in walk(e):
                    if x[0] in ("closure", "native"):
                        for r in x[2]:
                            if r[0] == "rref":
                                captures.append((b.id, idx, ("r", r[1])))
    cap_seeds = set()
    for bid, idx, v in captures:
        seed = use_site.get((bid, idx, v))
        if seed is None:
            continue
        cap_seeds.add(seed)
        L = v[1]
        seen_starts = set()
        work = [(bid, idx + 1)]
        while work:
            b0, i0 = work.pop()
            bb = F.bbs[b0]
            stopped = False
            for j in range(i0, len(bb.stmts)):
                st = bb.stmts[j]
                if st[0] == "close" and st[1] == L:
                    stopped = True
                    break
                if v in stmt_defs(st):
                    uf.union(seed, def_site[(b0, j, v)])
            if stopped:
                continue
            for sc in bb.succs():
                if sc not in seen_starts:
                    seen_starts.add(sc)
                    work.append((sc, 0))
    # web ids
    web_of_def = {}
    webinfo = {}
    for i in range(len(defs)):
        r = uf.find(i)
        web_of_def[i] = r
    for i, v in enumerate(defs):
        r = web_of_def[i]
        if r not in webinfo:
            webinfo[r] = {"var": v, "param": None, "captured": v in cap, "entry_used": False}
    for v, i in entry_def.items():
        if v[0] == "r" and v[1] <= nparams:
            webinfo[web_of_def[i]]["param"] = v[1]
    for w in webinfo.values():
        w["captured"] = False
    for sd in cap_seeds:
        webinfo[web_of_def[sd]]["captured"] = True
    # rewrite
    for b in F.bbs.values():
        new = []
        for idx, s in enumerate(b.stmts):
            def fu(e, _b=b.id, _idx=idx):
                if is_v(e):
                    d = use_site.get((_b, _idx, e))
                    if d is None:
                        return None
                    w = web_of_def[d]
                    if d == entry_def.get(e):
                        webinfo[w]["entry_used"] = True
                    return ("w", w)
                return None
            s2 = map_stmt(s, lambda e: subst(e, fu))
            # defs
            if s2[0] == "set" and is_v(s2[1]):
                w = web_of_def[def_site[(b.id, idx, s[1])]]
                s2 = ("set", ("w", w), s2[2])
            elif s2[0] == "mset":
                tg = []
                for t in s[1]:
                    if is_v(t):
                        tg.append(("w", web_of_def[def_site[(b.id, idx, t)]]))
                    else:
                        tg.append(subst(t, fu))
                s2 = ("mset", tuple(tg), s2[2])
            new.append(s2)
        b.stmts = new

        def ft(e, _b=b.id):
            if is_v(e):
                d = use_site.get((_b, "term", e))
                if d is None:
                    return None
                w = web_of_def[d]
                if d == entry_def.get(e):
                    webinfo[w]["entry_used"] = True
                return ("w", w)
            return None
        b.term = term_with_exprs(b.term, [subst(e, ft) for e in term_exprs(b.term)])
    return webinfo
