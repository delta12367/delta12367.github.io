"""Keep the number of simultaneously active locals under Luau's limit (200 per function).

1. Statement runs whose locals all die inside the run are wrapped in `do ... end`.
2. If a block still gets too close to the limit, the remaining locals declared in it are stored as
   fields of one table local (`local scope = {}` ... `scope.x = e`). Nested blocks reuse the table of
   their enclosing block, except loop bodies: those get their own table so that every iteration still
   creates fresh variables (closures created in a loop keep capturing per-iteration values).
"""
from ir import walk
import patterns

LIMIT = 150          # start grouping into do-blocks
SPILL_LIMIT = 190    # hard ceiling used for spilling (Luau errors at 200)
SPILL_BASE = -1000000
_spill_next = [SPILL_BASE]
_REFS = {}


def is_spill_table(w):
    return isinstance(w, int) and w <= SPILL_BASE


def refs_of_stmt(s):
    """all web ids referenced anywhere inside statement s (incl. nested blocks)"""
    hit = _REFS.get(id(s))
    if hit is not None and hit[0] is s:
        return hit[1]
    out = _refs_of_stmt(s)
    _REFS[id(s)] = (s, out)
    return out


def _refs_of_stmt(s):
    out = set()
    for e in patterns.stmt_own_exprs(s):
        for x in walk(e):
            if x[0] == "w":
                out.add(x[1])
    if s[0] == "local":
        out |= set(s[1])
    for cb in patterns.child_blocks(s):
        for t in cb:
            out |= refs_of_stmt(t)
    return out


def decl_of(s):
    """number of locals a statement adds to the current block"""
    k = s[0]
    if k == "local":
        return len(s[1])
    if k == "flagdecl":
        return 1
    if k == "ir" and s[1][0] == "setlist" and s[1][1][0] == "newtable":
        return 1   # emitted as `local _ = {...}`
    return 0


def decl_count(block):
    return sum(decl_of(s) for s in block)


def chunk(block):
    """group statement runs into do-blocks when all locals they declare die inside the run"""
    n = len(block)
    refs = [refs_of_stmt(s) for s in block]
    last = {}
    for i, r in enumerate(refs):
        for w in r:
            last[w] = i
    out = []
    i = 0
    while i < n:
        s = block[i]
        if s[0] != "local":
            out.append(s)
            i += 1
            continue
        j = i
        maxlast = i
        declared = 0
        best = None
        while j < n:
            sj = block[j]
            if sj[0] == "local":
                for w in sj[1]:
                    maxlast = max(maxlast, last.get(w, j))
                declared += len(sj[1])
            if j >= maxlast and declared > 0:
                best = j
                if declared >= 8 or j - i > 40:
                    break
            if maxlast >= n - 1 and j - i > 400:
                break
            j += 1
        if best is not None and best > i and not (i == 0 and best == n - 1):
            out.append(("do", block[i:best + 1]))
            i = best + 1
        else:
            out.append(s)
            i += 1
    return out


def spill_from(block, i, S):
    """turn every local declared in block[i:] into a field of table S"""
    spilled = set()
    for s in block[i:]:
        if s[0] == "local":
            spilled.update(s[1])
    fn = lambda e: ("spill", S, e[1]) if e[0] == "w" and e[1] in spilled else None
    out = []
    for s in block[i:]:
        if s[0] == "local":
            ws, init = s[1], s[2]
            if init is None:
                continue
            init = patterns.subst(init, fn)
            if len(ws) == 1:
                out.append(("ir", ("set", ("spill", S, ws[0]), init)))
            else:
                out.append(("ir", ("mset", tuple(("spill", S, w) for w in ws), init)))
            continue
        out.append(patterns.subst_stmt(s, fn))
    return out


def wrap_block(block, base, S, allow_chunk=True):
    """base = locals already active (enclosing blocks, parameters); S = inherited spill table or None"""
    if allow_chunk and base + decl_count(block) > LIMIT:
        block = chunk(block)
    # find the first local that would exceed the ceiling
    active = base
    cut = None
    for i, s in enumerate(block):
        d = decl_of(s)
        if s[0] == "local" and active + d + (0 if S is not None else 1) > SPILL_LIMIT:
            cut = i
            break
        active += d
    if cut is not None:
        prefix = list(block[:cut])
        if S is None:
            S = _spill_next[0]
            _spill_next[0] -= 1
            prefix.append(("local", [S], ("newtable", (), ())))
        block = prefix + spill_from(block, cut, S)
    out = []
    active = base
    for s in block:
        out.append(rebuild(s, active, S))
        active += decl_of(s)
    return out


def rebuild(s, active, S):
    k = s[0]
    if k == "if":
        return ("if", s[1], wrap_block(s[2], active, S), wrap_block(s[3], active, S))
    if k == "while":
        return ("while", s[1], wrap_block(s[2], active, None))
    if k == "repeat":
        # the until-condition may see body locals: never regroup; when spilling, rewrite the condition too
        probe = list(s[1]) + [("if", s[2], [], [])]
        res = wrap_block(probe, active, None, allow_chunk=False)
        return ("repeat", res[:-1], res[-1][1])
    if k == "fornum":
        return s[:5] + (wrap_block(s[5], active + 1, None),)
    if k == "forin":
        return s[:5] + (wrap_block(s[5], active + len(s[1]), None),)
    if k == "do":
        return ("do", wrap_block(s[1], active, S))
    return s


def apply(body, base=0):
    try:
        return wrap_block(body, base, None)
    finally:
        _REFS.clear()
