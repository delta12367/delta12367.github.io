"""Block exploration for one prototype (split out of lifter.py)."""
import collections
from interp import Scope, LuaError, BREAK, CONTINUE, LuaFunction
from lifter import (SE, Special, ITable, MultiSym, Captures, PathCtx, TrackDict, LoopHead, LiftError)


def lift(L, pid, verbose=False, max_retries=12):
    extra = collections.defaultdict(set)
    for attempt in range(max_retries):
        blocks, conflicts = lift_once(L, pid, extra)
        if not conflicts:
            return blocks
        for pc, v in conflicts:
            extra[pc].add(v)
        if verbose:
            print("retry", attempt, sorted(conflicts, key=repr)[:10])
    raise LiftError("could not stabilise block keys for proto %d" % pid)


def sig(v):
    if v is None or v is True or v is False:
        return ("c", v)
    if isinstance(v, (float, bytes)):
        return ("c", v)
    if isinstance(v, SE):
        return ("e", v.e)
    if isinstance(v, Special):
        return v.sig()
    if isinstance(v, ITable):
        return ("T",)
    return ("o", id(v))


def snapshot(vars_):
    memo = {}

    def cp(v):
        if isinstance(v, ITable):
            if id(v) in memo:
                return memo[id(v)]
            t = ITable()
            memo[id(v)] = t
            t.escaped = v.escaped
            t.meta = v.meta
            for k2, v2 in v.h.items():
                t.h[k2] = cp(v2)
            return t
        return v
    return {k: cp(v) for k, v in dict.items(vars_)}


def lift_once(L, pid, extra):
    I = L.I
    proto = L.tables[pid]
    I.protoid = pid
    I.blockid = 0
    I.ctx = PathCtx([])
    I.fscope = Scope(None)
    f = I.call(I.real_factory, [proto, Captures()])[0]
    if not isinstance(f, LuaFunction):
        raise LiftError("factory did not return a lua function")
    params, vararg, body = f.node.a
    main = None
    for s in body:
        if s.k in ("While", "Repeat"):
            main = s
            break
    I.main_loop_node = main
    blocks = {}
    variants = collections.defaultdict(list)
    reads_at = collections.defaultdict(set)
    incoming = collections.defaultdict(list)
    worklist = []

    def make_scope():
        sc = Scope(f.scope)
        sc.vars = TrackDict()
        sc.varargs = [MultiSym(("va",))]
        return sc

    def spill(vars_, pc):
        for k, v in list(dict.items(vars_)):
            if isinstance(v, SE) and v.e[0] != "k":
                name = ("v", int(pc), k)
                if v.e != name:
                    I.emit(("set", name, v.e))
                dict.__setitem__(vars_, k, SE(name))

    def run_paths(start_vars, mode, bid):
        I.blockid = bid
        stack = [[]]
        paths = []
        livein = set()
        while stack:
            dec = stack.pop()
            ctx = PathCtx(dec)
            I.ctx = ctx
            sc = make_scope()
            if start_vars is not None:
                for k2, v2 in snapshot(start_vars).items():
                    dict.__setitem__(sc.vars, k2, v2)
                sc.vars.reads = set()
                sc.vars.writes = set()
            I.fscope = sc
            I.mode = mode
            outcome = None
            try:
                if mode == "entry":
                    try:
                        r = I.exec_block(body, sc)
                        outcome = ("ret", list(r[1]) if r and r not in (BREAK, CONTINUE) else [])
                    except LoopHead:
                        outcome = ("head",)
                else:
                    loop_body = main.a[0] if main.k == "Repeat" else main.a[1]
                    inner = Scope(sc)
                    r = I.exec_block(loop_body, inner)
                    if r is None or r is CONTINUE:
                        outcome = ("head",)
                    elif r is BREAK:
                        idx = body.index(main)
                        r2 = I.exec_block(body[idx + 1:], sc)
                        outcome = ("ret", list(r2[1]) if r2 and r2 not in (BREAK, CONTINUE) else [])
                    else:
                        outcome = ("ret", list(r[1]))
            except LuaError as e:
                outcome = ("err", repr(e.value) + " @line " + str(getattr(e, "line", None)))
            except LiftError as e:
                outcome = ("lifterr", str(e) + " @line " + str(getattr(e, "line", None)))
            except RecursionError:
                outcome = ("lifterr", "recursion")
            while I.journal:
                tbl, kk, old = I.journal.pop()
                if old is None:
                    tbl.h.pop(kk, None)
                else:
                    tbl.h[kk] = old
            livein |= sc.vars.reads
            writes = set(sc.vars.writes)
            if outcome[0] == "head":
                pc = dict.get(sc.vars, "pc")
                spill(sc.vars, pc)
                outcome = ("head", dict(sc.vars))
            elif outcome[0] == "ret":
                try:
                    outcome = ("ret", I.args_exprs(outcome[1]))
                except LiftError as e:
                    outcome = ("lifterr", str(e))
            paths.append([ctx.taken, ctx.conds, ctx.ir, outcome, writes])
            for alt in ctx.alts:
                stack.append(alt)
            if len(paths) > 20000:
                raise LiftError("path explosion in block %d" % bid)
        return paths, livein

    def find_or_add(vars_):
        pc = vars_.get("pc")
        for bid, st in variants[pc]:
            if pc in reads_at:
                names = reads_at[pc] | extra[pc]
            else:
                names = set(st) | set(vars_)
            if all(sig(vars_.get(n)) == sig(st.get(n)) for n in names):
                incoming[bid].append(vars_)
                return bid
        bid = len(blocks) + 1
        blocks[bid] = None
        variants[pc].append((bid, vars_))
        incoming[bid].append(vars_)
        worklist.append((bid, vars_))
        return bid

    def resolve(paths):
        for p in paths:
            if p[3][0] == "head":
                p[3] = ("goto", find_or_add(p[3][1]))

    paths, livein = run_paths(None, "entry", 0)
    blocks[0] = {"pc": None, "paths": paths, "reads": livein, "state": None}
    resolve(paths)
    while worklist:
        bid, vars_ = worklist.pop()
        paths, livein = run_paths(vars_, "loop", bid)
        pc = vars_.get("pc")
        reads_at[pc] |= livein
        blocks[bid] = {"pc": pc, "paths": paths, "reads": livein, "state": vars_}
        resolve(paths)
        if len(blocks) > 60000:
            raise LiftError("too many blocks")
    live = {b: set(blocks[b]["reads"]) for b in blocks}
    changed = True
    while changed:
        changed = False
        for b, blk in blocks.items():
            cur = live[b]
            for p in blk["paths"]:
                if p[3][0] == "goto":
                    add = live[p[3][1]] - p[4]
                    if not add <= cur:
                        cur |= add
                        changed = True
    conflicts = set()
    for b, blk in blocks.items():
        if b == 0:
            continue
        rep = blk["state"]
        for st in incoming[b]:
            for v in live[b]:
                if sig(st.get(v)) != sig(rep.get(v)):
                    conflicts.add((blk["pc"], v))
    return blocks, conflicts
