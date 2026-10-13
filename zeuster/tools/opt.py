"""IR optimisation: call conversion, expression propagation, copy propagation, dead code elimination."""
import collections
from ir import (VAR_KINDS, is_var, children, rebuild, subst, walk, vars_read, has_effects, reads_mem, has_alloc, may_raise,
                stmt_exprs, stmt_with_exprs, stmt_defs, stmt_effects, term_exprs, term_with_exprs, liveness)

CALL_KINDS = ("callx", "callmx", "mcallx", "mcallmx")
import re as _re
_NAME = _re.compile(rb"^[A-Za-z_][A-Za-z0-9_]*$")
_KW = {b"and", b"break", b"do", b"else", b"elseif", b"end", b"false", b"for", b"function", b"if", b"in", b"local",
       b"nil", b"not", b"or", b"repeat", b"return", b"then", b"true", b"until", b"while", b"continue"}


def is_name(b):
    return _NAME.match(b) is not None and b not in _KW


def captured_regs(F):
    cap = set()
    for b in F.bbs.values():
        exprs = []
        for s in b.stmts:
            exprs.extend(stmt_exprs_any(s))
        exprs.extend(term_exprs(b.term))
        for e in exprs:
            for x in walk(e):
                if x[0] in ("closure", "native"):
                    for r in x[2]:
                        if r[0] == "rref":
                            cap.add(("r", r[1]))
                        elif r[0] == "rrefv" and r[1][0] in ("w", "r"):
                            cap.add(r[1])
    return cap


def stmt_exprs_any(s):
    if s[0] in ("mcopy", "fgnext", "close"):
        return []
    return stmt_exprs(s)


def map_stmt(s, fn):
    if s[0] in ("mcopy", "fgnext", "close"):
        return s
    return stmt_with_exprs(s, [fn(e) for e in stmt_exprs(s)])


def count_occ(e, var):
    n = 0
    for x in walk(e):
        if x == var:
            n += 1
    return n


def all_exprs(F):
    for b in F.bbs.values():
        for s in b.stmts:
            for e in stmt_exprs_any(s):
                yield e
        for e in term_exprs(b.term):
            yield e


KEEP_READS = True
STRICT_ORDER = True    # expressions that can raise keep their order relative to each other


class Optimizer:
    def __init__(self, F, nregs, log=None):
        self.F = F
        self.nregs = nregs
        self.cap = captured_regs(F)
        self.log = log if log is not None else []
        self.kept_reads = 0

    def run(self):
        self.prepare()
        self.inline_loop()

    def prepare(self):
        self.convert_calls()
        self.convert_multicopy()
        import ssaspill
        self.log.append("spill copies removed: %d" % ssaspill.run(self.F))

    def inline_loop(self):
        self.cap = captured_regs(self.F)
        import cfgfold
        for it in range(300):
            changed = False
            if cfgfold.fold_andor(self.F):
                changed = True
            if cfgfold.sink_common_tails(self.F):
                changed = True
            for b in self.F.bbs.values():
                if self.fold_tables(b):
                    changed = True
            live_in, live_out = liveness(self.F)
            for b in self.F.bbs.values():
                if self.inline_bb(b, live_out[b.id]):
                    changed = True
            if self.fold_msets():
                changed = True
            if not changed:
                break
        self.drop_mcopy()

    # ------------------------------------------------------------------ calls
    def convert_calls(self):
        F = self.F
        uses = collections.defaultdict(list)
        for e in all_exprs(F):
            for x in walk(e):
                if x[0] in ("sel", "multi", "count") and x[1][0] == "call":
                    uses[x[1][1]].append((x[0], x[2] if x[0] == "sel" else None))
        mode = {}
        for b in F.bbs.values():
            out = []
            for s in b.stmts:
                if s[0] != "call":
                    out.append(s)
                    continue
                cid, f, args = s[1], s[2], tuple(s[3])
                us = uses.get(cid, [])
                kinds = set(u[0] for u in us)
                if not us:
                    out.append(("callstat", ("callmx", f, args)))
                    continue
                if kinds == {"multi"}:
                    mode[cid] = "multi"
                    out.append(("set", ("x", "m", cid), ("callmx", f, args)))
                    continue
                if "count" in kinds:
                    self.log.append("call result count used")
                maxk = max([int(u[1]) for u in us if u[0] == "sel"] or [1])
                if "multi" in kinds:
                    self.log.append("call results used both as multi and single")
                mode[cid] = "sel"
                if maxk == 1:
                    out.append(("set", ("x", "c", cid, 1), ("callx", f, args)))
                else:
                    targets = tuple(("x", "c", cid, k) for k in range(1, maxk + 1))
                    out.append(("mset", targets, ("callmx", f, args)))
            b.stmts = out

        def fix(e):
            if e[0] == "sel" and e[1][0] == "call":
                return ("x", "c", e[1][1], int(e[2]))
            if e[0] == "multi" and e[1][0] == "call":
                cid = e[1][1]
                if mode.get(cid) == "multi":
                    return ("x", "m", cid)
                return ("x", "c", cid, 1)
            return None
        for b in F.bbs.values():
            b.stmts = [map_stmt(s, lambda e: subst(e, fix)) for s in b.stmts]
            b.term = term_with_exprs(b.term, [subst(e, fix) for e in term_exprs(b.term)])

    def convert_multicopy(self):
        """mcopy base src: registers base.. receive all values of src. Keep a marker statement; uses of those
        registers (rare) are rewritten to select() expressions by the liveness-aware pass below."""
        F = self.F
        for b in F.bbs.values():
            out = []
            for s in b.stmts:
                if s[0] == "multicopy":
                    out.append(("mcopy", s[1], s[2]))
                else:
                    out.append(s)
            b.stmts = out

    # ------------------------------------------------------------------ inlining
    def inline_bb(self, b, live_out):
        changed = False
        i = 0
        while i < len(b.stmts):
            s = b.stmts[i]
            if s[0] == "set" and is_var(s[1]) and s[1] not in self.cap:
                if self.try_inline(b, i, s[1], s[2], live_out):
                    changed = True
                    continue
            i += 1
        return changed

    def try_inline(self, b, i, var, E, live_out):
        stmts = b.stmts
        n = len(stmts)
        uses = []
        redefined = False
        j = i + 1
        while j < n:
            sj = stmts[j]
            if sj[0] == "mcopy" and var[0] == "r" and var[1] >= sj[1]:
                redefined = True
                break
            c = sum(count_occ(e, var) for e in stmt_exprs_any(sj))
            if c:
                uses.append((j, c))
            if var in stmt_defs(sj):
                redefined = True
                break
            j += 1
        if not redefined:
            c = sum(count_occ(e, var) for e in term_exprs(b.term))
            if c:
                uses.append(("term", c))
        live_after = (not redefined) and (var in live_out)
        total = sum(c for _, c in uses)
        if total == 2 and len(uses) == 1 and not live_after and self.self_pattern(b, uses[0][0], var):
            pos = uses[0][0]
            if self.safe_move(b, i, pos, E, var):
                self.replace_self(b, pos, var, E)
                del stmts[i]
                return True
        if total == 0:
            if live_after:
                return False
            if has_effects(E):
                if E[0] in CALL_KINDS:
                    stmts[i] = ("callstat", E)
                    return True
                return False
            if KEEP_READS and any(x[0] == "idx" for x in walk(E)) and not any(
                    x[0] in ("static", "regfile", "hostfn") for x in walk(E)):
                # an unused field read can still run __index (or fail on nil): keep it as `local _ = e`
                stmts[i] = ("callstat", E)
                self.kept_reads += 1
                return True
            del stmts[i]
            return True
        if live_after:
            return False
        atom = E[0] in ("k", "p", "builtin", "static", "hostfn") or (is_var(E) and E not in self.cap)
        multi_var = var[0] == "x" and var[1] == "m"
        if E[0] != "k" and any(self.used_in_capture(b, pos, var) for pos, c in uses):
            return False
        if total == 1:
            pos = uses[0][0]
            if self.safe_move(b, i, pos, E, var):
                self.replace_use(b, pos, var, E)
                del stmts[i]
                return True
            if not atom:
                return False
        if atom and not multi_var:
            evars = vars_read(E)
            done = []
            ok_all = True
            for pos, c in uses:
                endj = pos if pos != "term" else len(stmts)
                blocked = False
                for k in range(i + 1, endj):
                    sk = stmts[k]
                    if evars & set(stmt_defs(sk)):
                        blocked = True
                        break
                    if sk[0] == "mcopy" and any(v[0] == "r" and v[1] >= sk[1] for v in evars):
                        blocked = True
                        break
                if blocked:
                    ok_all = False
                    break
                done.append(pos)
            if not done:
                return False
            for pos in done:
                self.replace_use(b, pos, var, E)
            if ok_all:
                del stmts[i]
                return True
            return True
        return False

    def self_pattern(self, b, pos, var):
        exprs = term_exprs(b.term) if pos == "term" else stmt_exprs_any(b.stmts[pos])
        for e in exprs:
            for x in walk(e):
                if x[0] in ("callx", "callmx") and x[1][0] == "idx" and x[1][1] == var and x[2] and x[2][0] == var \
                        and x[1][2][0] == "k" and isinstance(x[1][2][1], bytes) and is_name(x[1][2][1]):
                    return True
        return False

    def replace_self(self, b, pos, var, E):
        def f(e):
            if e[0] in ("callx", "callmx") and e[1][0] == "idx" and e[1][1] == var and e[2] and e[2][0] == var:
                kind = "mcallx" if e[0] == "callx" else "mcallmx"
                return (kind, E, e[1][2][1], tuple(subst(a, f) for a in e[2][1:]))
            return None
        if pos == "term":
            b.term = term_with_exprs(b.term, [subst(e, f) for e in term_exprs(b.term)])
        else:
            b.stmts[pos] = map_stmt(b.stmts[pos], lambda e: subst(e, f))

    def fold_tables(self, b):
        """X = {} ; X.k = v ; X[1..] = setlist  ->  X = {..}"""
        changed = False
        st = b.stmts
        i = 0
        while i < len(st):
            s = st[i]
            if s[0] == "set" and is_var(s[1]) and s[2][0] == "newtable" and s[1] not in self.cap:
                X = s[1]
                arr = list(s[2][1])
                hs = list(s[2][2])
                j = i + 1
                while j < len(st):
                    sj = st[j]
                    if sj[0] == "set" and sj[1][0] == "idx" and sj[1][1] == X:
                        key, val = sj[1][2], sj[2]
                        if count_occ(key, X) or count_occ(val, X):
                            break
                        if key[0] == "k" and isinstance(key[1], float) and key[1] == len(arr) + 1 and not hs:
                            arr.append(val)
                        else:
                            if key[0] == "k" and key[1] is None:
                                break
                            hs.append((key, val))
                        j += 1
                        continue
                    if sj[0] == "setlist" and sj[1] == X and sj[2][0] == "k" and sj[2][1] == len(arr) + 1:
                        vals = sj[3]
                        if any(count_occ(v, X) for v in vals):
                            break
                        if hs and (any(has_effects(v) or reads_mem(v) for v in vals)
                                   or any(has_effects(v) for kk, v in hs)):
                            # positional items are emitted before hash items: folding would reorder calls
                            break
                        if hs:
                            # keep constructor order: positional items after hash ones is fine in Lua
                            pass
                        arr.extend(vals)
                        j += 1
                        continue
                    break
                if j > i + 1:
                    st[i] = ("set", X, ("newtable", tuple(arr), tuple(hs)))
                    del st[i + 1:j]
                    changed = True
            i += 1
        return changed

    def drop_mcopy(self):
        for b in self.F.bbs.values():
            b.stmts = [s for s in b.stmts if s[0] != "mcopy"]

    def used_in_capture(self, b, pos, var):
        exprs = term_exprs(b.term) if pos == "term" else stmt_exprs_any(b.stmts[pos])
        for e in exprs:
            for x in walk(e):
                if x[0] in ("closure", "native"):
                    for r in x[2]:
                        if r[0] in ("val", "rrefv") and r[1] == var:
                            return True
        return False

    def safe_move(self, b, i, pos, E, var=None):
        stmts = b.stmts
        end = pos if pos != "term" else len(stmts)
        evars = vars_read(E)
        mem = reads_mem(E) or may_raise(E)
        eff = has_effects(E)
        for k in range(i + 1, end):
            sk = stmts[k]
            if evars & set(stmt_defs(sk)):
                return False
            if sk[0] == "mcopy" and any(v[0] == "r" and v[1] >= sk[1] for v in evars):
                return False
            if (mem or eff) and stmt_effects(sk):
                return False
            if STRICT_ORDER and mem and any(reads_mem(e) or may_raise(e) for e in stmt_exprs_any(sk)):
                # both can raise (index of nil, arithmetic on a wrong type): keep their order
                return False
            if eff:
                for e in stmt_exprs_any(sk):
                    if reads_mem(e):
                        return False
        if var is not None and (mem or eff):
            # evaluation order inside the using statement: nothing that is evaluated before the
            # use may call (or, when E has side effects or may raise, read memory / raise)
            exprs = term_exprs(b.term) if pos == "term" else eval_order_exprs(stmts[pos])
            before = effects_before(exprs, var)
            if before is None:
                return False
            calls, reads = before
            if calls or ((eff or STRICT_ORDER) and reads):
                return False
        return True

    def replace_use(self, b, pos, var, E):
        f = lambda e: E if e == var else None
        if pos == "term":
            b.term = term_with_exprs(b.term, [subst(e, f) for e in term_exprs(b.term)])
        else:
            b.stmts[pos] = map_stmt(b.stmts[pos], lambda e: subst(e, f))

    # ------------------------------------------------------------------ mset folding
    def fold_msets(self):
        """mset (x1, x2) = call ; R5 = x1 ; R6 = x2   ->   mset (R5, R6) = call"""
        changed = False
        usecount = collections.Counter()
        for e in all_exprs(self.F):
            for x in walk(e):
                if x[0] == "x":
                    usecount[x] += 1
        for b in self.F.bbs.values():
            st = b.stmts
            for i, s in enumerate(st):
                if s is None or s[0] != "mset":
                    continue
                targets = list(s[1])
                for ti, t in enumerate(targets):
                    if t[0] != "x" or usecount[t] != 1:
                        continue
                    for j in range(i + 1, min(len(st), i + 3 + len(targets))):
                        sj = st[j]
                        if sj is None:
                            continue
                        if sj[0] == "set" and sj[2] == t and is_var(sj[1]) and sj[1] not in self.cap:
                            ok = sj[1] not in targets
                            for k in range(i + 1, j):
                                sk = st[k]
                                if sk is None:
                                    continue
                                if any(count_occ(e, sj[1]) for e in stmt_exprs_any(sk)) or sj[1] in stmt_defs(sk):
                                    ok = False
                            if ok:
                                targets[ti] = sj[1]
                                st[j] = None
                                changed = True
                            break
                        if any(count_occ(e, t) for e in stmt_exprs_any(sj)):
                            break
                st[i] = ("mset", tuple(targets), s[2])
            b.stmts = [x for x in st if x is not None]
        return changed


# ----------------------------------------------------------------------------- evaluation order
def eval_order_exprs(s):
    """expressions of a statement in the order Lua evaluates them"""
    k = s[0]
    if k == "set":
        t = s[1]
        if t[0] == "idx":
            return [t[1], t[2], s[2]]
        if t[0] == "g":
            return [t[1], s[2]]
        return [s[2]]
    return list(stmt_exprs_any(s))


def effects_before(exprs, var):
    """(calls, reads) evaluated before the first occurrence of var, or None if var does not occur"""
    state = {"calls": 0, "reads": 0, "found": False}

    def visit(e):
        if state["found"]:
            return
        if e == var:
            state["found"] = True
            return
        k = e[0]
        if k in ("callx", "callmx"):
            visit(e[1])
            for a in e[2]:
                visit(a)
            if not state["found"]:
                state["calls"] += 1
            return
        if k in ("mcallx", "mcallmx"):
            visit(e[1])
            for a in e[3]:
                visit(a)
            if not state["found"]:
                state["calls"] += 1
            return
        if k == "idx":
            visit(e[1])
            visit(e[2])
            if not state["found"]:
                state["reads"] += 1
            return
        if k == "g":
            visit(e[1])
            if not state["found"]:
                state["reads"] += 1
            return
        if k == "newtable":
            for a in e[1]:
                visit(a)
            for a, b in e[2]:
                visit(a)
                visit(b)
            return
        if (k == "bin" and e[1] not in ("==", "~=", "and", "or")) or (k == "un" and e[1] in ("#", "-")):
            for c in children(e):
                visit(c)
            if not state["found"]:
                state["reads"] += 1      # the operator itself can raise (counted with the reads)
            return
        for c in children(e):
            visit(c)
    for e in exprs:
        visit(e)
        if state["found"]:
            return state["calls"], state["reads"]
    return None
