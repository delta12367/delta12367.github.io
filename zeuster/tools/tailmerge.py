"""Merge duplicated basic blocks (identical statements & terminators), bottom-up."""
from ir import walk, subst, liveness, stmt_defs, term_exprs, VAR_KINDS
from opt import stmt_exprs_any, map_stmt


def canon_bb(b, live_out):
    """canonical signature of a BB: block-local temporaries are alpha-renamed"""
    local = {}
    defined = set()
    for s in b.stmts:
        for d in stmt_defs(s):
            defined.add(d)
    renam = {}
    for v in defined:
        if v not in live_out and v[0] in ("t", "x", "s", "w"):
            renam[v] = None

    def rn(e):
        if e in renam:
            if renam[e] is None:
                renam[e] = ("loc", len([1 for x in renam.values() if x is not None]))
            return renam[e]
        return None
    stmts = []
    for s in b.stmts:
        if s[0] == "set" and s[1] in renam:
            rhs = subst(s[2], rn)
            tgt = rn(s[1])
            stmts.append(("set", tgt, rhs))
        elif s[0] == "mset":
            stmts.append(("mset", tuple(rn(t) or t for t in s[1]), subst(s[2], rn)))
        else:
            stmts.append(map_stmt(s, lambda e: subst(e, rn)))
    t = b.term
    if t[0] == "br":
        term = ("br", subst(t[1], rn), t[2], t[3])
    elif t[0] == "ret":
        term = ("ret", tuple(subst(e, rn) for e in t[1]))
    else:
        term = t
    return (tuple(stmts), term)


def run(F):
    merged = 0
    for it in range(50):
        live_in, live_out = liveness(F)
        sigs = {}
        dup = {}
        for bid in sorted(F.bbs):
            b = F.bbs[bid]
            if bid == F.entry:
                continue
            try:
                sg = canon_bb(b, live_out[bid])
                hash(sg)
            except TypeError:
                continue
            if sg in sigs:
                dup[bid] = sigs[sg]
            else:
                sigs[sg] = bid
        if not dup:
            break
        for d, keep in dup.items():
            for p in list(F.bbs[d].preds):
                pb = F.bbs.get(p)
                if pb is None:
                    continue
                t = pb.term
                if t[0] == "jmp" and t[1] == d:
                    pb.term = ("jmp", keep)
                elif t[0] == "br":
                    pb.term = ("br", t[1], keep if t[2] == d else t[2], keep if t[3] == d else t[3])
        for d in dup:
            if d in F.bbs:
                del F.bbs[d]
                merged += 1
        F.compute_preds()
        from ir import simplify_cfg
        simplify_cfg(F)
    return merged
