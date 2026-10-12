"""Recover the original `do ... end` scopes from the VM's register allocation.

Luraph's compiler gives every local the next free register, like a Lua compiler, and frees the
registers of a block's locals when the block ends. So when a local is declared in a register
that an earlier local of the same block already holds (or a lower one), the earlier local's
scope - and that of every local declared after it - has ended. Each such scope that ends before
its enclosing block does becomes a `do ... end` block, provided nothing declared in it is used
afterwards (checked, so the rewrite never changes meaning).

Without this, every local of a long function body stays visible until the end of the body,
which runs into Luau's limit of 200 locals and forces the fallback in doblocks.py.
"""
import doblocks
import patterns
DEBUG = None


def _reg(webinfo, w):
    v = webinfo.get(w, {}).get("var")
    if v is not None and v[0] == "r":
        return int(v[1])
    return None


def _decl_regs(s, webinfo):
    """registers of the locals declared directly by statement s"""
    if s[0] != "local":
        return []
    return [r for r in (_reg(webinfo, w) for w in s[1]) if r is not None]


def _min_nested_reg(s, webinfo, memo):
    """lowest register of any local declared by s or inside its nested blocks"""
    key = id(s)
    if key in memo:
        return memo[key][1]
    best = None
    regs = _decl_regs(s, webinfo)
    if regs:
        best = min(regs)
    for cb in patterns.child_blocks(s):
        for t in cb:
            r = _min_nested_reg(t, webinfo, memo)
            if r is not None and (best is None or r < best):
                best = r
    memo[key] = (s, best)
    return best


def _declared(s):
    if s[0] == "local":
        return set(s[1])
    return set()


def _intervals(block, webinfo, memo):
    n = len(block)
    lows = [_min_nested_reg(s, webinfo, memo) for s in block]
    out = []
    for i, s in enumerate(block):
        regs = _decl_regs(s, webinfo)
        if not regs:
            continue
        r0 = min(regs)
        for j in range(i + 1, n):
            if lows[j] is not None and lows[j] <= r0:
                out.append((i, j))
                break
    return out


def hoist(block, webinfo, memo, stats):
    """`local x = f(a, b)` right after the scope of a and b, with x in a lower register than
    theirs: x was declared before that scope and assigned at its end. Declare x before the
    scope and turn the declaration into an assignment, so the scope can be closed."""
    for _ in range(len(block)):
        n = len(block)
        refs = [doblocks.refs_of_stmt(s) for s in block]
        last = {}
        for i, r in enumerate(refs):
            for w in r:
                last[w] = i
        changed = False
        for i, end in _intervals(block, webinfo, memo):
            closer = block[end]
            if closer[0] != "local" or closer[2] is None or end >= n - 1:
                continue
            declared = set()
            for k in range(i, end):
                if block[k][0] == "flagdecl":
                    declared = None
                    break
                declared |= _declared(block[k])
            if not declared:
                continue
            late = [w for w in declared if last.get(w, -1) >= end]
            if not late or any(last[w] > end for w in late):
                continue
            ws = closer[1]
            if any(x == ("w", w) for w in ws for x in _walk(closer[2])):
                continue
            if len(ws) == 1:
                assign = ("ir", ("set", ("w", ws[0]), closer[2]))
            else:
                assign = ("ir", ("mset", tuple(("w", w) for w in ws), closer[2]))
            block = block[:i] + [("local", list(ws), None)] + block[i:end] + [assign] + block[end + 1:]
            stats["hoist"] += 1
            changed = True
            break
        if not changed:
            return block
    return block


def _walk(e):
    from ir import walk
    return walk(e)


def scope_block(block, webinfo, memo, stats):
    n = len(block)
    block = [rebuild(s, webinfo, memo, stats) for s in block]
    if n < 3:
        return block
    block = hoist(block, webinfo, memo, stats)
    n = len(block)
    lows = [_min_nested_reg(s, webinfo, memo) for s in block]
    refs = [doblocks.refs_of_stmt(s) for s in block]
    last = {}
    for i, r in enumerate(refs):
        for w in r:
            last[w] = i
    # interval of each local declaration: [i, end) where end is the first later statement that
    # declares a local in a register <= this one
    intervals = []
    for i, s in enumerate(block):
        regs = _decl_regs(s, webinfo)
        if not regs:
            continue
        r0 = min(regs)
        end = None
        for j in range(i + 1, n):
            if lows[j] is not None and lows[j] <= r0:
                end = j
                break
        if end is None or end >= n:
            continue
        intervals.append((i, end))
    if not intervals:
        return block
    # keep the outermost interval per end point, and only valid ones
    ok = []
    for i, end in intervals:
        declared = set()
        bad = False
        for k in range(i, end):
            if block[k][0] == "flagdecl":
                bad = True
                break
            declared |= _declared(block[k])
        if bad or not declared:
            continue
        if any(last.get(w, -1) >= end for w in declared):
            if DEBUG is not None:
                bad_ws = [w for w in declared if last.get(w, -1) >= end]
                DEBUG.append((i, end, n, [(w, _reg(webinfo, w), last[w], block[last[w]][0]) for w in bad_ws][:4],
                              block[end][0], lows[end]))
            continue
        ok.append((i, end))
    if not ok:
        return block
    # laminar family: build nested do-blocks; drop an interval that ends where its parent ends
    ok.sort(key=lambda x: (x[0], -x[1]))

    def build(lo, hi, cands):
        out = []
        k = lo
        idx = 0
        while k < hi:
            # first candidate starting at k that fits and does not span the whole [lo, hi)
            pick = None
            while idx < len(cands) and cands[idx][0] < k:
                idx += 1
            j = idx
            while j < len(cands) and cands[j][0] == k:
                a, b = cands[j]
                if b <= hi and not (a == lo and b == hi) and b < hi:
                    pick = (a, b)
                    break
                j += 1
            if pick is None:
                out.append(block[k])
                k += 1
                continue
            a, b = pick
            inner = [c for c in cands if a <= c[0] and c[1] <= b and c != pick]
            stats["do"] += 1
            out.append(("do", build(a, b, inner)))
            k = b
        return out
    return build(0, n, ok)


def rebuild(s, webinfo, memo, stats):
    k = s[0]
    if k == "if":
        return ("if", s[1], scope_block(s[2], webinfo, memo, stats), scope_block(s[3], webinfo, memo, stats))
    if k == "while":
        return ("while", s[1], scope_block(s[2], webinfo, memo, stats))
    if k == "repeat":
        # the until-condition can see body locals: leave the body's scopes alone
        return s
    if k in ("fornum", "forin"):
        return s[:5] + (scope_block(s[5], webinfo, memo, stats),)
    if k == "do":
        return ("do", scope_block(s[1], webinfo, memo, stats))
    return s


def apply(body, webinfo):
    stats = {"do": 0, "hoist": 0}
    try:
        out = scope_block(body, webinfo, {}, stats)
    finally:
        doblocks._REFS.clear()
    return out, stats["do"]
