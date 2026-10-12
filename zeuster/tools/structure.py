"""Control-flow structuring: CFG -> nested statements (if / loop / break / continue / return)."""
import collections

CONT = "CONT"
BRK = "BRK"
EXIT = "EXIT"


def rpo(F):
    seen = set()
    order = []
    stack = [(F.entry, iter(F.bbs[F.entry].succs()))]
    seen.add(F.entry)
    while stack:
        n, it = stack[-1]
        nxt = None
        for s in it:
            if s not in seen:
                nxt = s
                break
        if nxt is None:
            order.append(n)
            stack.pop()
        else:
            seen.add(nxt)
            stack.append((nxt, iter(F.bbs[nxt].succs())))
    order.reverse()
    return order


def dominators(nodes, entry, preds):
    """Cooper-Harvey-Kennedy. nodes in RPO."""
    idx = {n: i for i, n in enumerate(nodes)}
    idom = {entry: entry}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n == entry:
                continue
            ps = [p for p in preds(n) if p in idom and p in idx]
            if not ps:
                continue
            new = ps[0]
            for p in ps[1:]:
                a, b = p, new
                while a != b:
                    while idx[a] > idx[b]:
                        a = idom[a]
                    while idx[b] > idx[a]:
                        b = idom[b]
                new = a
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom


def dominates(idom, a, b):
    while True:
        if a == b:
            return True
        nb = idom.get(b)
        if nb is None or nb == b:
            return False
        b = nb


class Structurer:
    def __init__(self, F):
        self.F = F
        self.order = rpo(F)
        self.rpo_idx = {n: i for i, n in enumerate(self.order)}
        self.idom = dominators(self.order, F.entry, lambda n: F.bbs[n].preds)
        self.find_loops()
        self.pdom_cache = {}
        self.emitted_loops = set()
        self.flagn = 0
        self.problems = []

    def succs(self, n):
        return self.F.bbs[n].succs()

    def find_loops(self):
        F = self.F
        loops = collections.defaultdict(set)
        for n in self.order:
            for s in self.succs(n):
                if dominates(self.idom, s, n):
                    # back edge n -> s
                    body = {s}
                    stack = [n]
                    while stack:
                        x = stack.pop()
                        if x in body:
                            continue
                        body.add(x)
                        for p in F.bbs[x].preds:
                            if p in self.rpo_idx:
                                stack.append(p)
                    loops[s] |= body
        self.loops = dict(loops)
        # loop follow
        self.follow = {}
        for h, body in self.loops.items():
            exits = []
            for n in body:
                for s in self.succs(n):
                    if s not in body and s not in exits:
                        exits.append(s)
            self.follow[h] = self.pick_follow(h, body, exits)

    def reach(self, start, limit_nodes=None):
        """forward reachability (back edges are not followed)"""
        if not hasattr(self, "_reach_cache"):
            self._reach_cache = {}
        if start in self._reach_cache:
            return self._reach_cache[start]
        seen = set()
        st = [start]
        while st:
            x = st.pop()
            if x in seen:
                continue
            seen.add(x)
            for s in self.succs(x):
                if dominates(self.idom, s, x):
                    continue
                st.append(s)
        self._reach_cache[start] = seen
        return seen

    def pick_follow(self, h, body, exits):
        if not exits:
            return None
        if len(exits) == 1:
            return exits[0]
        # candidate reachable from the most other exits (the merge point after the loop)
        reach = {}
        for e in exits:
            if e in self.loops and h in self.loops[e] and e != h:
                # header of an enclosing loop: reaching it means "next iteration", not forward flow
                reach[e] = {e}
            else:
                reach[e] = self.reach(e)
        best = None
        best_score = None
        for e in exits:
            score = sum(1 for o in exits if o != e and e in reach[o])
            # dead-end exits (cannot reach anything else) score low
            key = (score, -self.rpo_idx.get(e, 0))
            if best is None or key > best_score:
                best = e
                best_score = key
        # prefer exits that are not dead ends
        if best_score[0] == 0:
            alive = [e for e in exits if len(reach[e]) > 1 or self.F.bbs[e].term[0] not in ("ret", "err")]
            nondead = []
            for e in exits:
                r = reach[e]
                if any(self.F.bbs[x].term[0] == "jmp" or self.F.bbs[x].term[0] == "br" for x in r):
                    nondead.append(e)
            # choose the non-dead exit with the smallest rpo index (closest to the loop)
            cands = [e for e in exits if not self.dead_end(e)]
            if cands:
                best = min(cands, key=lambda e: self.rpo_idx.get(e, 0))
            else:
                best = min(exits, key=lambda e: self.rpo_idx.get(e, 0))
        return best

    def dead_end(self, e):
        """true if every path from e ends in return/error without joining the rest of the function"""
        if e in self.loops:
            return False
        for x in self.reach(e):
            b = self.F.bbs[x]
            for p in b.preds:
                if p not in self.reach(e) and x != e:
                    return False
        return True

    # ------------------------------------------------------------------ post dominators per context
    def ipdom(self, n, ctx):
        key = ctx.header if ctx else None
        if key not in self.pdom_cache:
            self.pdom_cache[key] = self.compute_pdom(ctx)
        return self.pdom_cache[key].get(n)

    def compute_pdom(self, ctx):
        F = self.F
        if ctx is None:
            nodes = set(self.order)
        else:
            nodes = set(ctx.body)
        # reverse graph successors (in region): map exits to virtual nodes
        def rsuccs(n):
            out = []
            b = F.bbs[n]
            if b.term[0] in ("ret", "err"):
                out.append(EXIT)
            for s in b.succs():
                if ctx is not None and s == ctx.header:
                    out.append(CONT)
                elif ctx is not None and s == ctx.follow:
                    out.append(BRK)
                elif s in nodes:
                    out.append(s)
                else:
                    out.append(EXIT)
            return out
        END = "END"
        allnodes = list(nodes) + [CONT, BRK, EXIT, END]
        succ = {n: rsuccs(n) for n in nodes}
        succ[CONT] = [END]
        succ[BRK] = [END]
        succ[EXIT] = [END]
        succ[END] = []
        pred = collections.defaultdict(list)
        for n, ss in succ.items():
            for s in ss:
                pred[s].append(n)
        # post-dominators = dominators on reversed graph rooted at END
        # order: reverse postorder of reversed graph from END
        seen = set()
        order = []
        stack = [(END, iter(pred[END]))]
        seen.add(END)
        while stack:
            x, it = stack[-1]
            nxt = None
            for s in it:
                if s not in seen:
                    nxt = s
                    break
            if nxt is None:
                order.append(x)
                stack.pop()
            else:
                seen.add(nxt)
                stack.append((nxt, iter(pred[nxt])))
        order.reverse()
        ipd = dominators(order, END, lambda x: succ[x])
        out = {}
        for n in nodes:
            p = ipd.get(n)
            out[n] = p
        return out

    # ------------------------------------------------------------------ emission
    def run(self):
        self.budget = 20000 + 30 * sum(len(b.stmts) + 1 for b in self.F.bbs.values())
        return self.seq(self.F.entry, None, None, set())

    def seq(self, n, ctx, stop, active):
        out = []
        first = True
        visited = set()
        while True:
            self.budget -= 1
            if self.budget < 0:
                self.problems.append("budget")
                out.append(("comment", "structuring budget exhausted"))
                return out
            if n is None:
                return out
            if n == stop:
                return out
            starting_loop = ctx is not None and n == ctx.header and ctx.at_header
            if ctx is not None:
                if n == ctx.header and not starting_loop:
                    out.append(("continue",))
                    return out
                if n == ctx.follow:
                    out.append(("break",))
                    return out
                c = ctx.parent
                hit = None
                while c is not None:
                    if n == c.header:
                        hit = (c, "cont")
                        break
                    if n == c.follow:
                        hit = (c, "brk")
                        break
                    c = c.parent
                if hit is not None:
                    self.flagn += 1
                    flag = self.flagn
                    hit[0].owned_flags.append(flag)
                    ctx.pending.append((flag, hit[0], hit[1]))
                    out.append(("flagset", flag))
                    out.append(("break",))
                    return out
            if n in visited:
                self.problems.append("revisit %r" % n)
                out.append(("goto", n))
                return out
            visited.add(n)
            if starting_loop:
                ctx.at_header = False
            if n in self.loops and not starting_loop:
                if n in active:
                    self.problems.append("loop reentry %r" % n)
                    out.append(("goto", n))
                    return out
                out.extend(self.loop(n, ctx, active))
                f = self.follow.get(n)
                first = False
                n = f
                continue
            if ctx is not None and n not in ctx.body:
                # exit path from the loop body that does not go to the follow: inline it
                pass
            first = False
            b = self.F.bbs[n]
            out.extend(("ir", s) for s in b.stmts)
            t = b.term
            if t[0] == "ret":
                out.append(("return", t[1]))
                return out
            if t[0] == "err":
                out.append(("error", t[1]))
                return out
            if t[0] == "jmp":
                n = t[1]
                continue
            # conditional
            merge = self.ipdom(n, ctx)
            if merge in (CONT, BRK, EXIT, "END"):
                merge = None
            if merge is not None and ctx is not None and merge not in ctx.body:
                merge = None
            if merge is not None and stop is not None and merge != stop and not self.before(merge, stop, ctx):
                merge = None
            if merge is None:
                # no post-dominator (some paths return early): merge at the first node both branches reach
                m2 = self.alt_merge(t[2], t[3], ctx, stop)
                if m2 is not None:
                    merge = m2
            tb = self.seq(t[2], ctx, merge if merge is not None else stop, active)
            fb = self.seq(t[3], ctx, merge if merge is not None else stop, active)
            out.append(("if", t[1], tb, fb))
            if merge is None:
                return out
            n = merge

    def alt_merge(self, a, b, ctx, stop):
        """first node (in reverse post order) reachable from both a and b along forward edges,
        staying inside the current loop body"""
        import heapq
        idx = self.rpo_idx
        if a == b:
            return None
        side = {}
        heap = []
        for start, bit in ((a, 1), (b, 2)):
            if start not in idx:
                return None
            side[start] = side.get(start, 0) | bit
            heapq.heappush(heap, (idx[start], start))
        seen = set()
        budget = 200000
        while heap:
            budget -= 1
            if budget < 0:
                return None
            _, x = heapq.heappop(heap)
            if x in seen:
                continue
            seen.add(x)
            if side.get(x) == 3:
                if ctx is not None and (x not in ctx.body or x == ctx.header or x == ctx.follow):
                    return None
                if stop is not None and x != stop and not self.before(x, stop, ctx):
                    return None
                if x == stop:
                    return None
                return x
            if x == stop:
                continue
            if ctx is not None and (x == ctx.header or x == ctx.follow or x not in ctx.body):
                continue
            for s2 in self.succs(x):
                if dominates(self.idom, s2, x):
                    continue  # back edge
                side[s2] = side.get(s2, 0) | side[x]
                heapq.heappush(heap, (idx.get(s2, 1 << 30), s2))
        return None

    def before(self, a, b, ctx):
        """is node a strictly before stop b (b reachable from a)"""
        return b in self.reach(a)

    def loop(self, h, outer, active):
        body = self.loops[h]
        ctx = LoopCtx(h, self.follow.get(h), body)
        ctx.parent = outer
        ctx.at_header = True
        stmts = self.seq(h, ctx, None, active | {h})
        if ctx.owned_flags:
            stmts = [("flagdecl", f) for f in ctx.owned_flags] + stmts
        res = [("loop", stmts)]
        for flag, target, kind in ctx.pending:
            if target is outer:
                act = ("continue",) if kind == "cont" else ("break",)
            else:
                act = ("break",)
                if outer is not None:
                    outer.pending.append((flag, target, kind))
            res.append(("if", ("flagvar", flag), [act], []))
        return res


class LoopCtx:
    def __init__(self, header, follow, body):
        self.header = header
        self.follow = follow
        self.body = body
        self.at_header = True
        self.parent = None
        self.pending = []
        self.owned_flags = []


# ---------------------------------------------------------------------------- AST cleanup
def ends_with_jump(stmts):
    if not stmts:
        return False
    k = stmts[-1][0]
    if k in ("break", "continue", "return", "error", "goto"):
        return True
    if k == "if":
        return ends_with_jump(stmts[-1][2]) and ends_with_jump(stmts[-1][3])
    return False


def cleanup(stmts, in_loop_tail=False):
    """recursive simplifications: drop trailing continue, flatten guard ifs, while/repeat detection"""
    out = []
    for i, s in enumerate(stmts):
        k = s[0]
        last = (i == len(stmts) - 1)
        if k == "if":
            tb = cleanup(s[2], in_loop_tail and last)
            fb = cleanup(s[3], in_loop_tail and last)
            cond = s[1]
            if not tb and fb:
                cond = ("un", "not", cond)
                tb, fb = fb, []
            if not tb and not fb:
                # condition with no effect
                continue
            # guard-clause flattening: if c then X(jump) else Y end -> if c then X end; Y
            if fb and ends_with_jump(tb):
                out.append(("if", cond, tb, []))
                out.extend(fb)
                continue
            if fb and ends_with_jump(fb) and not ends_with_jump(tb):
                out.append(("if", ("un", "not", cond), fb, []))
                out.extend(tb)
                continue
            out.append(("if", cond, tb, fb))
        elif k == "loop":
            body = cleanup(s[1], True)
            out.append(make_loop(body))
        elif k == "continue" and last and in_loop_tail:
            continue
        else:
            out.append(s)
    # re-run flatten for nested results
    return out


def has_continue(stmts):
    for s in stmts:
        if s[0] == "continue":
            return True
        if s[0] == "if" and (has_continue(s[2]) or has_continue(s[3])):
            return True
    return False


def is_for_header(body):
    """loop bodies that start with numeric/generic for machinery must stay `while true` so the
    pattern matcher can recognise them (they must not become repeat-until)"""
    if not body:
        return False
    s0 = body[0]
    if s0[0] == "ir" and s0[1][0] == "fgnext":
        return True
    if (len(body) > 1 and s0[0] == "ir" and s0[1][0] == "set" and s0[1][1][0] == "w"
            and s0[1][2][0] == "bin" and s0[1][2][1] == "+" and body[1][0] == "if"
            and body[1][2] == [("break",)] and not body[1][3]):
        return True
    return False


def make_loop(body):
    if is_for_header(body):
        return ("while", ("k", True), body)
    # while: first statement is `if c then break end`
    if body and body[0][0] == "if" and body[0][2] == [("break",)] and not body[0][3]:
        return ("while", ("un", "not", body[0][1]), body[1:])
    if body and body[0][0] == "if" and body[0][3] == [("break",)] and not body[0][2]:
        return ("while", body[0][1], body[1:])
    # repeat: last statement `if c then break end` and no continue
    if body and body[-1][0] == "if" and body[-1][2] == [("break",)] and not body[-1][3] and not has_continue(body[:-1]):
        return ("repeat", body[:-1], body[-1][1])
    return ("while", ("k", True), body)


# ---------------------------------------------------------------------------- short-circuit conditions
def neg(c):
    if c[0] == "un" and c[1] == "not":
        return c[2]
    return ("un", "not", c)


def _walk(e):
    from ir import walk
    return walk(e)


def merge_conditions(F):
    """A: if c1 then B else X ; B (empty, single pred): if c2 then T else X  ->  A: if c1 and c2 then T else X  (etc)"""
    count = 0
    changed = True
    while changed:
        changed = False
        F.compute_preds()
        for A in list(F.bbs.values()):
            if A.id not in F.bbs or A.term[0] != "br":
                continue
            c1, t1, f1 = A.term[1], A.term[2], A.term[3]
            if any(x[0] == "fgok" for x in _walk(c1)):
                # keep generic-for iterator checks separate so the for-in pattern stays recognisable
                continue
            done = False
            for side in ("t", "f"):
                bid = t1 if side == "t" else f1
                other = f1 if side == "t" else t1
                if bid == A.id or bid == F.entry:
                    continue
                B = F.bbs.get(bid)
                if B is None or B.stmts or B.term[0] != "br" or len(B.preds) != 1:
                    continue
                c2, t2, f2 = B.term[1], B.term[2], B.term[3]
                if bid in (t2, f2):
                    continue
                if side == "t":
                    if f2 == other:
                        new = ("br", ("and", c1, c2), t2, other)
                    elif t2 == other:
                        new = ("br", ("and", c1, neg(c2)), f2, other)
                    else:
                        continue
                else:
                    if t2 == other:
                        new = ("br", ("or", c1, c2), other, f2)
                    elif f2 == other:
                        new = ("br", ("or", c1, neg(c2)), other, t2)
                    else:
                        continue
                A.term = new
                del F.bbs[bid]
                for s in B.succs():
                    if s in F.bbs:
                        F.bbs[s].preds.discard(bid)
                        F.bbs[s].preds.add(A.id)
                changed = True
                count += 1
                done = True
                break
    F.compute_preds()
    return count
