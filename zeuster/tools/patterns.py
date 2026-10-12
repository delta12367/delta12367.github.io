"""AST-level pattern recognition: numeric for, generic for, condition cleanups."""
import collections, math
from ir import walk, subst
from opt import stmt_exprs_any


def child_blocks(s):
    k = s[0]
    if k == "if":
        return [s[2], s[3]]
    if k == "while":
        return [s[2]]
    if k == "repeat":
        return [s[1]]
    if k in ("fornum", "forin"):
        return [s[5]]
    if k == "do":
        return [s[1]]
    return []


def stmt_own_exprs(s):
    k = s[0]
    if k == "ir":
        st = s[1]
        out = list(stmt_exprs_any(st))
        if st[0] == "set":
            out.append(st[1])
        if st[0] == "mset":
            out.extend(st[1])
        return out
    if k in ("if", "while"):
        return [s[1]]
    if k == "repeat":
        return [s[2]]
    if k == "return":
        return list(s[1])
    if k == "fornum":
        return [("w", s[1])] + [e for e in (s[2], s[3], s[4]) if e is not None]
    if k == "forin":
        return [("w", w) for w in s[1]] + list(s[2])
    if k == "local":
        out = [("w", w) for w in s[1]]
        if s[2] is not None:
            out.append(s[2])
        return out
    return []


def count_refs(body):
    cnt = collections.Counter()

    def visit(block):
        for s in block:
            for e in stmt_own_exprs(s):
                for x in walk(e):
                    if x[0] == "w":
                        cnt[x[1]] += 1
            for cb in child_blocks(s):
                visit(cb)
    visit(body)
    return cnt


def assigned_in(block, w):
    for s in block:
        if s[0] == "ir":
            st = s[1]
            if st[0] == "set" and st[1] == ("w", w):
                return True
            if st[0] == "mset" and ("w", w) in st[1]:
                return True
        for cb in child_blocks(s):
            if assigned_in(cb, w):
                return True
    return False


def is_set(s, target=None):
    return s[0] == "ir" and s[1][0] == "set" and s[1][1][0] == "w" and (target is None or s[1][1] == target)


def strip_plus0(e):
    if e[0] == "bin" and e[1] == "+" and e[3] == ("k", 0.0):
        return e[2]
    return e


def nice_number(x):
    if not isinstance(x, float) or math.isinf(x) or x != x:
        return x
    r = round(x, 9)
    if abs(r - x) < 1e-9 * max(1.0, abs(x)):
        return float(r) if r != int(r) else float(int(r))
    return x


def match_fornum(block, i, cnt):
    """block[i] is the while loop; look for counter init before it"""
    loop = block[i]
    if loop[0] != "while" or loop[1] != ("k", True):
        return None
    body = loop[2]
    if len(body) < 2:
        return None
    s0 = body[0]
    if not is_set(s0):
        return None
    N = s0[1][1]
    e = s0[1][2]
    if not (e[0] == "bin" and e[1] == "+" and e[2][0] == "w"):
        return None
    K = e[2]
    step = e[3]
    if step[0] == "w" and step == K:
        return None
    s1 = body[1]
    if s1[0] != "if" or s1[2] != [("break",)] or s1[3]:
        return None
    cond = s1[1]
    if cond[0] != "un" or cond[1] != "not":
        return None
    c = cond[2]
    if c[0] != "bin" or c[2] != N:
        return None
    if step[0] == "k" and isinstance(step[1], float):
        if step[1] > 0 and c[1] != "<=":
            return None
        if step[1] < 0 and c[1] != ">=":
            return None
        if step[1] == 0:
            return None
    else:
        return None
    limit = c[3]
    # loop variable copy and counter update
    j = 2
    V = None
    kupd = False
    while j < len(body) and j < 5:
        sj = body[j]
        if is_set(sj) and sj[1][2] == N:
            if sj[1][1] == K:
                kupd = True
            elif V is None:
                V = sj[1][1]
            else:
                break
            j += 1
            continue
        break
    if not kupd:
        return None
    rest = body[j:]
    # counter init: closest preceding assignment to K in the same block
    init_idx = None
    for k in range(i - 1, max(-1, i - 4), -1):
        if is_set(block[k], K):
            init_idx = k
            break
    if init_idx is None:
        return None
    init = block[init_idx][1][2]
    # K, N must only be used by the loop machinery - or, inside the body, as the loop variable
    # (after `K = N` both hold the current value)
    k_rest = _count_in(rest, K[1])
    n_rest = _count_in(rest, N[1])
    expected_K = 1 + 1 + 1 + k_rest   # init def, increment read, update def
    if cnt[K[1]] != expected_K:
        return None
    n_uses = 1 + 1 + 1 + (1 if V is not None else 0) + n_rest  # def, cond, k update, var copy
    if cnt[N[1]] != n_uses:
        return None
    if k_rest or n_rest:
        if assigned_in(rest, K[1]) or assigned_in(rest, N[1]) or _captured_by_ref(rest, K[1]):
            return None
        if V is None:
            V = N
        fn = lambda e: V if e[0] == "w" and e[1] in (K[1], N[1]) and e != V else None
        rest = subst_block(rest, fn)
    # limit/step loop invariant
    for ex in (limit, step):
        for x in walk(ex):
            if x[0] == "w" and assigned_in(rest, x[1]):
                return None
    if V is not None and assigned_in(rest, V[1]):
        return None
    # start value
    if init[0] == "bin" and init[1] == "-" and init[3] == step:
        start = strip_plus0(init[2])
    elif init[0] == "k" and isinstance(init[1], float):
        start = ("k", nice_number(init[1] + step[1]))
    else:
        start = ("bin", "+", init, step)
    var = V[1] if V is not None else None
    return init_idx, var, start, strip_plus0(limit), step, rest


def _captured_by_ref(block, w):
    for x in walk_block(block):
        if x[0] in ("closure", "native"):
            for r in x[2]:
                if (r[0] == "rref" and r[1] == w) or (r[0] == "rrefv" and r[1] == ("w", w)):
                    return True
    return False


def _dyn_cond(c, N):
    """(S <= 0 and N >= L) or (0 < S and N <= L)  ->  (L, S)"""
    if c[0] != "or":
        return None
    a, b = c[1], c[2]
    if a[0] != "and" or b[0] != "and":
        return None
    a1, a2, b1, b2 = a[1], a[2], b[1], b[2]
    if not (a1[0] == "bin" and a1[1] == "<=" and a1[3] == ("k", 0.0)):
        return None
    S = a1[2]
    if not (a2[0] == "bin" and a2[1] == ">=" and a2[2] == N):
        return None
    L = a2[3]
    if not (b1 == ("bin", "<", ("k", 0.0), S) and b2 == ("bin", "<=", N, L)):
        return None
    return L, S


def match_fornum_dyn(block, i, cnt):
    """numeric for with a variable step (see loops.fold_dynamic_fornum)"""
    loop = block[i]
    if loop[0] != "while" or loop[1] != ("k", True):
        return None
    body = loop[2]
    if len(body) < 2 or not is_set(body[0]):
        return None
    N, e = body[0][1][1], body[0][1][2]
    if not (e[0] == "bin" and e[1] == "+" and e[2][0] == "w"):
        return None
    K, S = e[2], e[3]
    s1 = body[1]
    if s1[0] != "if" or s1[2] != [("break",)] or s1[3]:
        return None
    cond = s1[1]
    if cond[0] != "un" or cond[1] != "not":
        return None
    m = _dyn_cond(cond[2], N)
    if m is None or m[1] != S:
        return None
    L = m[0]
    j = 2
    V = None
    kupd = False
    while j < len(body) and j < 5:
        sj = body[j]
        if is_set(sj) and sj[1][2] == N:
            if sj[1][1] == K:
                kupd = True
            elif V is None:
                V = sj[1][1]
            else:
                break
            j += 1
            continue
        break
    if not kupd:
        return None
    rest = body[j:]
    # K = init, and copies L = L0, S = S0 directly before the loop
    lookback = range(i - 1, max(-1, i - 6), -1)
    init_idx = next((k for k in lookback if is_set(block[k], K)), None)
    if init_idx is None or cnt[K[1]] != 3:
        return None
    if cnt[N[1]] != 1 + 2 + 1 + (1 if V is not None else 0):
        return None
    drop = [init_idx]
    step, limit = S, L
    if S[0] == "w":
        sidx = next((k for k in lookback if is_set(block[k], S)), None)
        if sidx is not None and cnt[S[1]] == 4:
            step = block[sidx][1][2]
            drop.append(sidx)
        elif cnt[S[1]] != 3 or assigned_in(rest, S[1]):
            return None
    if L[0] == "w":
        lidx = next((k for k in lookback if is_set(block[k], L)), None)
        if lidx is not None and cnt[L[1]] == 3:
            limit = block[lidx][1][2]
            drop.append(lidx)
        elif assigned_in(rest, L[1]):
            return None
    for ex in (limit, step):
        for x in walk(ex):
            if x[0] == "w" and assigned_in(rest, x[1]):
                return None
    if V is not None and assigned_in(rest, V[1]):
        return None
    # the copies must not be read between their definition and the loop
    for k in range(min(drop), i):
        if k in drop:
            continue
        for ex in stmt_own_exprs(block[k]):
            for x in walk(ex):
                if x in (K, S, L):
                    return None
    init = block[init_idx][1][2]
    if init[0] == "bin" and init[1] == "-" and init[3] in (S, step):
        start = strip_plus0(init[2])
    else:
        start = ("bin", "+", init, step)
    var = V[1] if V is not None else None
    return sorted(drop), var, start, limit, step, rest


def ends_jump(stmts):
    if not stmts:
        return False
    k = stmts[-1][0]
    if k in ("return", "error", "break", "continue", "goto"):
        return True
    if k == "if":
        return bool(stmts[-1][3]) and ends_jump(stmts[-1][2]) and ends_jump(stmts[-1][3])
    return False


def ends_exit(stmts):
    """ends in return/error on every path (never falls through, never break/continue)"""
    if not stmts:
        return False
    k = stmts[-1][0]
    if k in ("return", "error"):
        return True
    if k == "if":
        return bool(stmts[-1][3]) and ends_exit(stmts[-1][2]) and ends_exit(stmts[-1][3])
    if k == "do":
        return ends_exit(stmts[-1][1])
    return False


def has_loop_jump(block, kinds=("break", "continue")):
    """break/continue statements that belong to the current loop level (not nested loops)"""
    for s in block:
        if s[0] in kinds:
            return True
        if s[0] == "flagset":
            return True
        if s[0] == "if" and (has_loop_jump(s[2], kinds) or has_loop_jump(s[3], kinds)):
            return True
        if s[0] == "do" and has_loop_jump(s[1], kinds):
            return True
    return False


def replace_breaks(block, repl):
    out = []
    for s in block:
        k = s[0]
        if k == "break":
            out.extend(repl)
            continue
        if k == "if":
            s = ("if", s[1], replace_breaks(s[2], repl), replace_breaks(s[3], repl))
        elif k == "do":
            s = ("do", replace_breaks(s[1], repl))
        out.append(s)
    return out


def match_forin(block, i, cnt):
    """returns (nvars, vars, exprs, rest, exit_stmts) or None.
    exit_stmts is None for the canonical `if not ok then break end` check; otherwise it holds the code
    that runs when the iterator is exhausted (when it is not simply `break`)."""
    s = block[i]
    if not (s[0] == "ir" and s[1][0] == "fginit"):
        return None
    if i + 1 >= len(block):
        return None
    loop = block[i + 1]
    if loop[0] != "while" or loop[1] != ("k", True):
        return None
    fid = s[1][1]
    body = list(loop[2])
    nvars = None
    vars_ = {}
    j = 0
    saw_next = False
    saw_check = False
    exit_stmts = None
    notok = ("un", "not", ("fgok", fid))
    while j < len(body) and j < 12:
        sj = body[j]
        if sj[0] == "ir" and sj[1][0] == "fgnext" and sj[1][1] == fid and not saw_next:
            saw_next = True
            nvars = sj[1][2]
            j += 1
            continue
        if is_set(sj) and sj[1][2][0] == "fgv" and sj[1][2][1] == fid:
            vars_[sj[1][2][2]] = sj[1][1][1]
            j += 1
            continue
        if saw_next and not saw_check and sj[0] == "if" and not sj[3] and sj[1] == notok:
            if sj[2] == [("break",)]:
                saw_check = True
                j += 1
                continue
            if ends_jump(sj[2]) and not has_loop_jump(sj[2][:-1]):
                last = sj[2][-1][0]
                if last in ("return", "error") and not has_loop_jump(sj[2]):
                    exit_stmts = ("exit", sj[2])
                elif last == "break":
                    exit_stmts = ("fall", sj[2][:-1])
                else:
                    break
                saw_check = True
                j += 1
                continue
        break
    if not (saw_next and saw_check):
        return None
    rest = body[j:]
    for x in walk_block(rest):
        if x[0] in ("fgok", "fgv") and x[1] == fid:
            return None
    if exit_stmts is not None:
        for x in walk_block(exit_stmts[1]):
            if x[0] in ("fgok", "fgv") and x[1] == fid:
                return None
    exprs = [s[1][2], s[1][3], s[1][4]]
    return nvars, vars_, exprs, rest, exit_stmts


def walk_stmts(block):
    for s in block:
        yield s
        for cb in child_blocks(s):
            yield from walk_stmts(cb)


def walk_block(block):
    for s in block:
        for e in stmt_own_exprs(s):
            yield from walk(e)
        for cb in child_blocks(s):
            yield from walk_block(cb)


class Ctx:
    def __init__(self):
        self.next_dummy = -1


def sink_common_tails(block):
    """if c then A; X else B; X end  ->  if c then A else B end; X   (X = identical non-jump IR statements)"""
    out = []
    for s in block:
        k = s[0]
        if k == "if":
            tb, fb = sink_common_tails(s[2]), sink_common_tails(s[3])
            moved = []
            while tb and fb and tb[-1] == fb[-1] and tb[-1][0] == "ir" and tb[-1][1][0] in ("fginit", "set", "callstat", "mset"):
                moved.append(tb[-1])
                tb = tb[:-1]
                fb = fb[:-1]
            if moved and (tb or fb):
                out.append(("if", s[1], tb, fb))
                out.extend(reversed(moved))
                continue
            if moved:
                # both branches became empty: keep the condition evaluation only if it can matter
                out.append(("if", s[1], tb, fb))
                out.extend(reversed(moved))
                continue
            s = ("if", s[1], tb, fb)
        elif k == "while":
            s = ("while", s[1], sink_common_tails(s[2]))
        elif k == "repeat":
            s = ("repeat", sink_common_tails(s[1]), s[2])
        elif k in ("fornum", "forin"):
            s = s[:5] + (sink_common_tails(s[5]),)
        elif k == "do":
            s = ("do", sink_common_tails(s[1]))
        out.append(s)
    return out


# ---------------------------------------------------------------------------- rotated generic for
def strip_close(block):
    out = []
    for st in block:
        if st[0] == "ir" and st[1][0] == "close":
            continue
        k = st[0]
        if k == "if":
            st = ("if", st[1], strip_close(st[2]), strip_close(st[3]))
        elif k == "while":
            st = ("while", st[1], strip_close(st[2]))
        elif k == "repeat":
            st = ("repeat", strip_close(st[1]), st[2])
        elif k in ("fornum", "forin"):
            st = st[:5] + (strip_close(st[5]),)
        elif k == "do":
            st = ("do", strip_close(st[1]))
        out.append(st)
    return out


def _defined_ws(block):
    out = set()
    for st in walk_stmts(block):
        if st[0] == "ir":
            x = st[1]
            if x[0] == "set" and x[1][0] == "w":
                out.add(x[1][1])
            elif x[0] == "mset":
                for t in x[1]:
                    if t[0] == "w":
                        out.add(t[1])
    return out


def alpha_equal(A, B, extra_a=(), extra_b=()):
    """structural equality of two statement lists up to a consistent renaming of variables that are
    defined inside them (or listed in extra_a/extra_b). Returns the renaming {a: b} or None."""
    da, db = _defined_ws(A) | set(extra_a), _defined_ws(B) | set(extra_b)
    m = {}
    used_b = {}

    def eq(x, y):
        if isinstance(x, tuple) and isinstance(y, tuple) and len(x) == 2 and len(y) == 2 and x[0] == "w" and y[0] == "w":
            a, b = x[1], y[1]
            if a in m:
                return m[a] == b
            if b in used_b and used_b[b] != a:
                return False
            if a == b or (a in da and b in db):
                m[a] = b
                used_b[b] = a
                return True
            return False
        if type(x) is not type(y):
            return False
        if isinstance(x, (tuple, list)):
            return len(x) == len(y) and all(eq(p, q) for p, q in zip(x, y))
        return x == y
    if not eq(list(A), list(B)):
        return None
    return m


def _count_in(block, w):
    n = 0
    for x in walk_block(block):
        if x == ("w", w):
            n += 1
    for st in walk_stmts(block):
        if st[0] == "local":
            n += list(st[1]).count(w)
    return n


def _match_step(block, j, fid):
    """fgnext(fid); a_k = fgv(fid, k)...  starting at j -> (end_index, nvars, {k: w})"""
    if j >= len(block):
        return None
    s0 = block[j]
    if not (s0[0] == "ir" and s0[1][0] == "fgnext" and s0[1][1] == fid):
        return None
    nvars = s0[1][2]
    vs = {}
    j += 1
    while j < len(block) and is_set(block[j]) and block[j][1][2][0] == "fgv" and block[j][1][2][1] == fid:
        vs[block[j][1][2][2]] = block[j][1][1][1]
        j += 1
    return j, nvars, vs


def _match_copies(block, j, srcs):
    """P_k = a_k copies for the step outputs -> (end_index, {k: P})"""
    inv = {w: k for k, w in srcs.items()}
    ps = {}
    while j < len(block) and is_set(block[j]) and block[j][1][2][0] == "w" and block[j][1][2][1] in inv:
        k = inv[block[j][1][2][1]]
        if k in ps:
            break
        ps[k] = block[j][1][1][1]
        j += 1
    return j, ps


def rotate_forin(block, ctx, webinfo, cnt):
    """bottom-up: fginit; step; if ok then P = a; while true do BODY; step; if not ok then break end; P = b end; T end
    -> forin(P...) BODY end; T"""
    block = [rotate_children(s, ctx, webinfo, cnt) for s in block]
    out = []
    i = 0
    while i < len(block):
        s = block[i]
        r = _rotated_at(block, i, ctx, webinfo, cnt, out)
        if r is not None:
            new_stmts, consumed = r
            out.extend(new_stmts)
            i += consumed
            continue
        out.append(s)
        i += 1
    return out


def rotate_children(s, ctx, webinfo, cnt):
    k = s[0]
    if k == "if":
        return ("if", s[1], rotate_forin(s[2], ctx, webinfo, cnt), rotate_forin(s[3], ctx, webinfo, cnt))
    if k == "while":
        return ("while", s[1], rotate_forin(s[2], ctx, webinfo, cnt))
    if k == "repeat":
        return ("repeat", rotate_forin(s[1], ctx, webinfo, cnt), s[2])
    if k in ("fornum", "forin"):
        return s[:5] + (rotate_forin(s[5], ctx, webinfo, cnt),)
    if k == "do":
        return ("do", rotate_forin(s[1], ctx, webinfo, cnt))
    return s


def _rotated_at(block, i, ctx, webinfo, cnt, out):
    s = block[i]
    if not (s[0] == "ir" and s[1][0] == "fginit"):
        return None
    fid = s[1][1]
    D = max(1, getattr(ctx, "fid_dups", {}).get(fid, 1))
    st = _match_step(block, i + 1, fid)
    if st is None:
        return None
    j, nvars, avars = st
    if j >= len(block) or block[j][0] != "if":
        return None
    ifs = block[j]
    c = ifs[1]
    while c[0] == "un" and c[1] == "not" and c[2][0] == "un" and c[2][1] == "not":
        c = c[2][2]
    if c == ("fgok", fid):
        tb, fb = ifs[2], ifs[3]
    elif c == ("un", "not", ("fgok", fid)):
        tb, fb = ifs[3], ifs[2]
    else:
        return None
    c_end, PA = _match_copies(tb, 0, avars)
    widx = None
    for idx in range(c_end, len(tb)):
        if tb[idx][0] == "while" and tb[idx][1] == ("k", True):
            widx = idx
            break
    if widx is None:
        return None
    prefixA = list(tb[c_end:widx])
    LB = list(tb[widx][2])
    T1 = list(tb[widx + 1:])
    T2 = list(fb)
    consumed_after = 0
    if T1 or T2:
        if not T2 and ends_exit(T1):
            # post-loop code duplicated: once inside the if (after the loop), once after the if
            T2 = list(block[j + 1:])
            consumed_after = len(T2)
        ren = alpha_equal(strip_close(T1), strip_close(T2))
        if ren is None:
            return None
        for a, b in ren.items():
            if a != b and (cnt.get(a, 0) != D * _count_in(T1, a) or cnt.get(b, 0) != D * _count_in(T2, b)):
                return None
    # loop latch: SUFFIX ; step ; if not ok then break end ; copies ; PREFIX (peeled copy)
    k = None
    for idx in range(len(LB) - 1, -1, -1):
        x = LB[idx]
        if x[0] == "ir" and x[1][0] == "fgnext" and x[1][1] == fid:
            k = idx
            break
    if k is None:
        return None
    st2 = _match_step(LB, k, fid)
    if st2 is None:
        return None
    j2, nv2, bvars = st2
    if nv2 != nvars or set(bvars) != set(avars):
        return None
    if j2 >= len(LB) or LB[j2][0] != "if" or LB[j2][2:] != ([("break",)], []):
        return None
    c2 = LB[j2][1]
    while c2[0] == "un" and c2[1] == "not" and c2[2][0] == "un" and c2[2][1] == "not":
        c2 = c2[2][2]
    if c2 != ("un", "not", ("fgok", fid)):
        return None
    c2_end, PB = _match_copies(LB, j2 + 1, bvars)
    prefixB = list(LB[c2_end:])
    if set(PA) != set(PB) or set(PA) != set(avars):
        return None
    headA = list(tb[:c_end]) + prefixA
    headB = list(LB[j2 + 1:c2_end]) + prefixB
    ren2 = alpha_equal(headA, headB, avars.values(), bvars.values())
    if ren2 is None:
        return None
    for kk in avars:
        if ren2.get(avars[kk]) != bvars[kk]:
            return None
    SUFFIX = LB[:k]
    # renamed variables (other than the shared ones) must be private to the two copies
    for a, b in ren2.items():
        if a == b:
            continue
        if cnt.get(b, 0) != D * (_count_in(headB, b) + (1 if b in bvars.values() else 0)):
            return None
        if cnt.get(a, 0) != D * (_count_in(headA, a) + (1 if a in avars.values() else 0)):
            return None
    for x in walk_block(SUFFIX + prefixA):
        if x[0] in ("fgok", "fgv") and x[1] == fid:
            return None
    BODY = prefixA + SUFFIX
    for w in PA.values():
        if assigned_in(BODY, w):
            return None
    names = []
    for kk in range(1, (nvars or 2) + 1):
        if kk in PA:
            names.append(PA[kk])
        else:
            names.append(ctx.next_dummy)
            webinfo[ctx.next_dummy] = {"dummy": True}
            ctx.next_dummy -= 1
    while len(names) > 1 and isinstance(names[-1], int) and names[-1] < 0:
        names.pop()
    ex = simplify_iter([s[1][2], s[1][3], s[1][4]], out)
    hint = None
    if ex and ex[0][0] in ("callx", "callmx") and ex[0][1] == ("g", ("k", b"ipairs")):
        hint = "ipairs"
    loop = ("forin", names, ex, None, hint, BODY)
    return [loop] + T1, (j - i) + 1 + consumed_after


def apply(body, webinfo):
    body = sink_common_tails(body)
    cnt = count_refs(body)
    ctx = Ctx()
    ctx.fid_dups = collections.Counter(st[1][1] for st in walk_stmts(body) if st[0] == "ir" and st[1][0] == "fginit")
    body = rotate_forin(body, ctx, webinfo, cnt)
    cnt = count_refs(body)
    return transform(body, cnt, ctx, webinfo)


def transform(block, cnt, ctx, webinfo):
    out = []
    i = 0
    block = list(block)
    while i < len(block):
        s = block[i]
        m = match_forin(block, i, cnt)
        tail_after = None
        if m and m[4] is not None:
            # iterator exhaustion does more than `break`: rebuild as  for .. do REST' end; EXIT
            kind, ex = m[4]
            post = block[i + 2:]
            rest = m[3]
            breaks = has_loop_jump(rest, ("break",)) or any(s2[0] == "flagset" for s2 in walk_stmts(rest))
            if kind == "exit":
                if breaks:
                    if not ends_exit(post) or has_loop_jump(post):
                        m = None
                    else:
                        rest = replace_breaks(rest, post)
                if m:
                    tail_after = list(ex)
            else:  # "fall": run ex, then continue with post
                if breaks and ex:
                    if not ends_exit(post) or has_loop_jump(post):
                        m = None
                    else:
                        rest = replace_breaks(rest, post)
                        tail_after = list(ex) + list(post)
                else:
                    tail_after = list(ex) + list(post)
            if m:
                m = (m[0], m[1], m[2], rest, None)
        if m:
            nvars, vars_, exprs, rest, _ = m
            names = []
            for k in range(1, (nvars or 2) + 1):
                if k in vars_:
                    names.append(vars_[k])
                else:
                    names.append(ctx.next_dummy)
                    webinfo[ctx.next_dummy] = {"dummy": True}
                    ctx.next_dummy -= 1
            # trim trailing unused dummies (keep at least one var)
            while len(names) > 1 and isinstance(names[-1], int) and names[-1] < 0:
                names.pop()
            ex = simplify_iter(exprs, out)
            hint = None
            if ex and ex[0][0] in ("callx", "callmx") and ex[0][1] == ("g", ("k", b"ipairs")):
                hint = "ipairs"
            out.append(("forin", names, ex, None, hint, transform(rest, cnt, ctx, webinfo)))
            if tail_after is not None:
                out.extend(transform(tail_after, cnt, ctx, webinfo))
                return out
            i += 2
            continue
        if s[0] == "while":
            m = match_fornum_dyn(block, i, cnt)
            if m:
                drop, var, start, limit, step, rest = m
                ok = True
                for di in drop:
                    pos = len(out) - (i - di)
                    if not (0 <= pos < len(out) and out[pos] is block[di]):
                        ok = False
                if ok:
                    positions = sorted((len(out) - (i - di) for di in drop), reverse=True)
                    for pos in positions:
                        del out[pos]
                    if var is None:
                        var = ctx.next_dummy
                        webinfo[var] = {"dummy": True}
                        ctx.next_dummy -= 1
                    out.append(("fornum", var, start, limit, step, transform(rest, cnt, ctx, webinfo)))
                    i += 1
                    continue
            m = match_fornum(block, i, cnt)
            if m:
                init_idx, var, start, limit, step, rest = m
                # remove the init statement from already-emitted output
                pos = len(out) - (i - init_idx)
                if 0 <= pos < len(out) and out[pos] is block[init_idx]:
                    del out[pos]
                    if var is None:
                        var = ctx.next_dummy
                        webinfo[var] = {"dummy": True}
                        ctx.next_dummy -= 1
                    out.append(("fornum", var, start, limit, step, transform(rest, cnt, ctx, webinfo)))
                    i += 1
                    continue
        out.append(transform_children(s, cnt, ctx, webinfo))
        i += 1
    return out


def simplify_iter(exprs, out):
    """(f, nil, nil) -> f ; mset(a,b,c) = call ; fginit(a,b,c) -> call"""
    f, st, ctl = exprs
    if out and out[-1][0] == "ir" and out[-1][1][0] == "mset":
        ms = out[-1][1]
        if list(ms[1]) == [f, st, ctl] or list(ms[1])[:3] == [f, st, ctl]:
            out.pop()
            return [ms[2]]
    if st == ("k", None) and ctl == ("k", None):
        return [f]
    if ctl == ("k", None):
        return [f, st]
    return [f, st, ctl]


def transform_children(s, cnt, ctx, webinfo):
    k = s[0]
    if k == "if":
        return ("if", s[1], transform(s[2], cnt, ctx, webinfo), transform(s[3], cnt, ctx, webinfo))
    if k == "while":
        return ("while", s[1], transform(s[2], cnt, ctx, webinfo))
    if k == "repeat":
        return ("repeat", transform(s[1], cnt, ctx, webinfo), s[2])
    if k in ("fornum", "forin"):
        return s[:5] + (transform(s[5], cnt, ctx, webinfo),)
    return s


# ---------------------------------------------------------------------------- post (after locals placed)
def simp_cond(e):
    """double negation and comparison negation"""
    if e[0] == "un" and e[1] == "not":
        inner = e[2]
        if inner[0] == "un" and inner[1] == "not":
            return simp_cond(inner[2])
        if inner[0] == "bin" and inner[1] == "==":
            return ("bin", "~=", inner[2], inner[3])
        if inner[0] == "bin" and inner[1] == "~=":
            return ("bin", "==", inner[2], inner[3])
        if inner[0] == "and":
            return ("or", simp_cond(("un", "not", inner[1])), simp_cond(("un", "not", inner[2])))
        if inner[0] == "or":
            return ("and", simp_cond(("un", "not", inner[1])), simp_cond(("un", "not", inner[2])))
    if e[0] in ("and", "or"):
        return (e[0], simp_cond(e[1]), simp_cond(e[2]))
    return e


def post(body):
    out = []
    for s in body:
        k = s[0]
        if k == "if":
            out.append(("if", simp_cond(s[1]), post(s[2]), post(s[3])))
        elif k == "while":
            out.append(("while", simp_cond(s[1]), post(s[2])))
        elif k == "repeat":
            out.append(("repeat", post(s[1]), simp_cond(s[2])))
        elif k in ("fornum", "forin"):
            out.append(s[:5] + (post(s[5]),))
        elif k == "do":
            out.append(("do", post(s[1])))
        else:
            out.append(s)
    return out


def subst_stmt(s, fn):
    k = s[0]
    if k == "ir":
        from opt import map_stmt
        st = s[1]
        st2 = map_stmt(st, lambda e: subst(e, fn))
        if st2[0] == "set":
            st2 = ("set", subst(st2[1], fn), st2[2])
        elif st2[0] == "mset":
            st2 = ("mset", tuple(subst(t, fn) for t in st2[1]), st2[2])
        return ("ir", st2)
    if k == "if":
        return ("if", subst(s[1], fn), subst_block(s[2], fn), subst_block(s[3], fn))
    if k == "while":
        return ("while", subst(s[1], fn), subst_block(s[2], fn))
    if k == "repeat":
        return ("repeat", subst_block(s[1], fn), subst(s[2], fn))
    if k == "return":
        return ("return", tuple(subst(e, fn) for e in s[1]))
    if k == "fornum":
        return ("fornum", s[1], subst(s[2], fn), subst(s[3], fn), subst(s[4], fn) if s[4] is not None else None,
                subst_block(s[5], fn))
    if k == "forin":
        return ("forin", s[1], [subst(e, fn) for e in s[2]], s[3], s[4], subst_block(s[5], fn))
    if k == "local":
        return ("local", s[1], subst(s[2], fn) if s[2] is not None else None)
    if k == "do":
        return ("do", subst_block(s[1], fn))
    return s


def subst_block(block, fn):
    return [subst_stmt(s, fn) for s in block]


def assigned_any(block, w):
    for s in block:
        if s[0] == "ir":
            st = s[1]
            if st[0] == "set" and st[1] == ("w", w):
                return True
            if st[0] == "mset" and ("w", w) in st[1]:
                return True
        if s[0] == "local" and s[2] is not None and w in s[1]:
            pass
        for cb in child_blocks(s):
            if assigned_any(cb, w):
                return True
    return False


def drop_loopvar_copies(body, loopvars):
    """for k, v in t do local x = k ... end  ->  use k directly"""
    changed = True
    while changed and body:
        changed = False
        for idx in range(min(3, len(body))):
            s = body[idx]
            if s[0] == "local" and len(s[1]) == 1 and s[2] is not None and s[2][0] == "w" and s[2][1] in loopvars:
                X, V = s[1][0], s[2][1]
                rest = body[idx + 1:]
                if assigned_any(rest, X) or assigned_any(rest, V):
                    continue
                fn = lambda e, X=X, V=V: ("w", V) if e == ("w", X) else None
                body = body[:idx] + subst_block(rest, fn)
                changed = True
                break
    return body


_orig_post = post


def post(body):
    body = _orig_post(body)
    return post_loops(body)


def post_loops(body):
    out = []
    for s in body:
        k = s[0]
        if k == "forin":
            lv = set(w for w in s[1] if not (isinstance(w, int) and w < 0))
            inner = drop_loopvar_copies(post_loops(s[5]), lv)
            out.append(s[:5] + (inner,))
        elif k == "fornum":
            inner = drop_loopvar_copies(post_loops(s[5]), {s[1]})
            out.append(s[:5] + (inner,))
        elif k == "if":
            out.append(("if", s[1], post_loops(s[2]), post_loops(s[3])))
        elif k == "while":
            out.append(("while", s[1], post_loops(s[2])))
        elif k == "repeat":
            out.append(("repeat", post_loops(s[1]), s[2]))
        elif k == "do":
            out.append(("do", post_loops(s[1])))
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------------------- table constructor folding (AST)
def _uses_w(e, w):
    for x in walk(e):
        if x == ("w", w):
            return True
    return False


def fold_setlist_locals(body):
    """local X = {..}; X[n..] = setlist(...)  ->  local X = {.., ...}"""
    out = []
    for s in body:
        k = s[0]
        if k == "if":
            s = ("if", s[1], fold_setlist_locals(s[2]), fold_setlist_locals(s[3]))
        elif k == "while":
            s = ("while", s[1], fold_setlist_locals(s[2]))
        elif k == "repeat":
            s = ("repeat", fold_setlist_locals(s[1]), s[2])
        elif k in ("fornum", "forin"):
            s = s[:5] + (fold_setlist_locals(s[5]),)
        elif k == "do":
            s = ("do", fold_setlist_locals(s[1]))
        if (k == "ir" and s[1][0] == "setlist" and out and out[-1][0] == "local" and len(out[-1][1]) == 1
                and out[-1][2] is not None and out[-1][2][0] == "newtable"):
            w = out[-1][1][0]
            tgt, start, vals = s[1][1], s[1][2], s[1][3]
            arr, hs = out[-1][2][1], out[-1][2][2]
            if (tgt == ("w", w) and start[0] == "k" and start[1] == len(arr) + 1
                    and not any(_uses_w(v, w) for v in vals)):
                out[-1] = ("local", out[-1][1], ("newtable", tuple(arr) + tuple(vals), hs))
                continue
        out.append(s)
    return out


_post_loops_prev = post


def post(body):
    return fold_setlist_locals(_post_loops_prev(body))
