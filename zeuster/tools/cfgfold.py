"""CFG-level expression recovery: and/or diamonds, common tail sinking."""
from ir import is_var, simplify_cfg


def fold_andor(F):
    """A: ... br X -> M / B ;  B: X = b ; jmp M   ==>  A: ... X = X or b ; jmp M"""
    count = 0
    changed = True
    while changed:
        changed = False
        F.compute_preds()
        for A in list(F.bbs.values()):
            if A.id not in F.bbs:
                continue
            t = A.term
            if t[0] != "br":
                continue
            cond, tt, ff = t[1], t[2], t[3]
            neg = False
            X = cond
            if cond[0] == "un" and cond[1] == "not":
                X = cond[2]
                neg = True
            if not is_var(X):
                continue
            # 'or' : branch to M when X truthy, else B assigns
            for kind, M, B in (("or", tt, ff), ("and", ff, tt)):
                if neg:
                    kind = "and" if kind == "or" else "or"
                if B == A.id or M == A.id or B == M:
                    continue
                Bb = F.bbs.get(B)
                if Bb is None or len(Bb.preds) != 1 or Bb.term != ("jmp", M) or len(Bb.stmts) != 1:
                    continue
                s = Bb.stmts[0]
                if s[0] != "set" or s[1] != X:
                    continue
                A.stmts.append(("set", X, (kind, X, s[2])))
                A.term = ("jmp", M)
                del F.bbs[B]
                count += 1
                changed = True
                break
    if count:
        F.compute_preds()
        simplify_cfg(F)
    return count


def sink_common_tails(F):
    """if all predecessors of M end with the same statement and jump to M, move it into M"""
    count = 0
    changed = True
    while changed:
        changed = False
        F.compute_preds()
        for M in list(F.bbs.values()):
            preds = [F.bbs[p] for p in M.preds if p in F.bbs]
            if len(preds) < 2 or M.id == F.entry:
                continue
            if not all(p.term == ("jmp", M.id) and p.stmts for p in preds):
                continue
            last = preds[0].stmts[-1]
            if not all(p.stmts[-1] == last for p in preds):
                continue
            if last[0] in ("close",):
                continue
            for p in preds:
                p.stmts.pop()
            M.stmts.insert(0, last)
            count += 1
            changed = True
    if count:
        simplify_cfg(F)
    return count
