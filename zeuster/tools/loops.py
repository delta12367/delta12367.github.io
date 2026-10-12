"""CFG-level loop normalisation done before structuring.

Numeric `for` loops whose step is not a compile-time constant are lifted as

    H: N = K + S ; if S <= 0 then goto A else goto B
    A: if not (N >= L) then goto EXIT else goto BODY
    B: if not (N <= L) then goto EXIT else goto BODY

The two empty test blocks are folded into H with a single side-effect free condition

    H: N = K + S ; if (S <= 0 and N >= L) or (S > 0 and N <= L) then goto BODY else goto EXIT

which the AST pattern matcher (patterns.match_fornum_dyn) turns back into `for i = a, b, s do`.
"""
from ir import has_effects


def _unnot(c):
    """returns (positive_condition, negated?)"""
    if c[0] == "un" and c[1] == "not":
        return c[2], True
    return c, False


def _cmp_target(blk, N):
    """block term br(cond, x, y) where cond is (not)(N op L): returns (op, L, body, exit)"""
    t = blk.term
    if t[0] != "br":
        return None
    c, neg = _unnot(t[1])
    if c[0] != "bin" or c[1] not in ("<=", ">=") or c[2] != N:
        return None
    if neg:
        # not (N op L) -> exit is the true target
        return c[1], c[3], t[3], t[2]
    return c[1], c[3], t[2], t[3]


def fold_dynamic_fornum(F):
    count = 0
    F.compute_preds()
    for H in list(F.bbs.values()):
        if H.id not in F.bbs or H.term[0] != "br" or not H.stmts:
            continue
        last = H.stmts[-1]
        if not (last[0] == "set" and last[1][0] == "w" and last[2][0] == "bin" and last[2][1] == "+"):
            continue
        N = last[1]
        K, S = last[2][2], last[2][3]
        cond = H.term[1]
        if not (cond[0] == "bin" and cond[1] == "<=" and cond[2] == S and cond[3] == ("k", 0.0)):
            continue
        if S[0] not in ("w", "u") or K[0] != "w":
            continue
        A = F.bbs.get(H.term[2])
        B = F.bbs.get(H.term[3])
        if A is None or B is None or A is B or A.stmts or B.stmts:
            continue
        if A.preds != {H.id} or B.preds != {H.id}:
            continue
        ta = _cmp_target(A, N)
        tb = _cmp_target(B, N)
        if ta is None or tb is None:
            continue
        if ta[0] != ">=" or tb[0] != "<=" or ta[1] != tb[1] or ta[2] != tb[2] or ta[3] != tb[3]:
            continue
        L = ta[1]
        if has_effects(L):
            continue
        body, exit_ = ta[2], ta[3]
        cont = ("or", ("and", ("bin", "<=", S, ("k", 0.0)), ("bin", ">=", N, L)),
                ("and", ("bin", "<", ("k", 0.0), S), ("bin", "<=", N, L)))
        H.term = ("br", cont, body, exit_)
        del F.bbs[A.id]
        del F.bbs[B.id]
        count += 1
    if count:
        F.compute_preds()
    return count
