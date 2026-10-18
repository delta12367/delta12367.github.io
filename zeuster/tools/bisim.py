"""Merge lifted block variants that behave identically (bisimulation / partition refinement).

The lifter keeps separate copies ("variants") of a VM pc when some interpreter-local value differs
between incoming paths (for example the VM's top-of-stack index). When those values do not change the
emitted code, the copies are exact duplicates and only make the output longer (code appears 2-4 times,
loops get split). Two blocks are merged when
  * they belong to the same pc,
  * their paths have the same decisions, conditions and IR (block-local temporaries renamed), and
  * their successors are (recursively) equivalent.
Assignments to spill variables that are never read anywhere are dropped first; they are what usually
makes otherwise identical variants differ.
"""
import collections


def _is_spill(x):
    return isinstance(x, tuple) and len(x) == 3 and x[0] == "v"


def _collect_reads(x, out, skip_target=False):
    """all spill variables appearing in x (conservative read set)"""
    stack = [x]
    while stack:
        y = stack.pop()
        if isinstance(y, tuple):
            if _is_spill(y):
                out.add(y)
                continue
            stack.extend(y)
        elif isinstance(y, list):
            stack.extend(y)


def drop_dead_spills(blocks):
    changed = True
    removed = 0
    while changed:
        changed = False
        reads = set()
        for b in blocks.values():
            for p in b["paths"]:
                _collect_reads(p[1], reads)
                for d, st in p[2]:
                    if st[0] == "set" and _is_spill(st[1]):
                        _collect_reads(st[2], reads)
                    else:
                        _collect_reads(st, reads)
                if p[3][0] != "goto":
                    _collect_reads(p[3], reads)
        for b in blocks.values():
            for p in b["paths"]:
                new_ir = [(d, st) for d, st in p[2]
                          if not (st[0] == "set" and _is_spill(st[1]) and st[1] not in reads)]
                if len(new_ir) != len(p[2]):
                    removed += len(p[2]) - len(new_ir)
                    p[2][:] = new_ir if isinstance(p[2], list) else new_ir
                    changed = True
    return removed


def _regs_in(x, out):
    stack = [x]
    while stack:
        y = stack.pop()
        if isinstance(y, tuple):
            if len(y) == 2 and y[0] == "r" and isinstance(y[1], int):
                out.add(y[1])
                continue
            if len(y) == 2 and y[0] == "rref" and isinstance(y[1], int):
                out.add(y[1])
                continue
            stack.extend(y)
        elif isinstance(y, list):
            stack.extend(y)


def _has_any(x, kinds):
    stack = [x]
    while stack:
        y = stack.pop()
        if isinstance(y, tuple):
            if y and y[0] in kinds:
                return True
            stack.extend(y)
        elif isinstance(y, list):
            stack.extend(y)
    return False


_IMPURE = ("call", "callx", "callmx", "mcallx", "mcallmx", "idx", "g", "p", "va", "multi", "count", "closure")  # index reads can run __index; parameter copies fix the arity; unused functions stay in the source


def drop_dead_regs(blocks):
    """remove register assignments with a side-effect-free value that no later code reads
    (scratch registers of Luraph's first-run resolvers)"""
    allregs = set()
    captured = set()
    for b in blocks.values():
        for p in b["paths"]:
            for d, st in p[2]:
                if _has_any(st, ("regfile", "rL", "multicopy", "mcopy")):
                    return 0
                _regs_in(st, allregs)
                stack = [st]
                while stack:
                    y = stack.pop()
                    if isinstance(y, tuple):
                        if len(y) == 2 and y[0] == "rref":
                            captured.add(y[1])
                        elif y and y[0] == "close":
                            return 0
                        else:
                            stack.extend(y)
                    elif isinstance(y, list):
                        stack.extend(y)
            _regs_in(p[1], allregs)
            if p[3][0] != "goto":
                _regs_in(p[3], allregs)

    def path_uses(p):
        """registers read on the path before being written, and registers written. Condition j of the
        path is evaluated after the IR entries recorded with fewer than j+1 decisions."""
        used, defd = set(), set()
        conds = p[1]
        nxt = 0
        for d, st in p[2]:
            while nxt < len(conds) and nxt < d:
                r = set()
                _regs_in(conds[nxt], r)
                used |= r - defd
                nxt += 1
            r = set()
            if st[0] == "set" and isinstance(st[1], tuple) and st[1][0] == "r":
                _regs_in(st[2], r)
                used |= r - defd
                defd.add(st[1][1])
            else:
                _regs_in(st, r)
                used |= r - defd
        while nxt < len(conds):
            r = set()
            _regs_in(conds[nxt], r)
            used |= r - defd
            nxt += 1
        if p[3][0] != "goto":
            r = set()
            _regs_in(p[3], r)
            used |= r - defd
        return used, defd
    info = {bid: [path_uses(p) for p in b["paths"]] for bid, b in blocks.items()}
    live_in = {bid: set() for bid in blocks}
    changed = True
    while changed:
        changed = False
        for bid, b in blocks.items():
            acc = set()
            for p, (used, defd) in zip(b["paths"], info[bid]):
                out = set(live_in.get(p[3][1], ())) if p[3][0] == "goto" else (set(allregs) if p[3][0] != "ret" else set())
                acc |= used | (out - defd)
            if not acc <= live_in[bid]:
                live_in[bid] |= acc
                changed = True
    removed = 0
    for bid, b in blocks.items():
        # paths of one block share the IR emitted before their first differing decision: a statement is
        # identified by (decisions taken before it, position among those) and only dropped when it is dead
        # on every path that executes it
        dead_votes = collections.defaultdict(list)
        keyed = []
        for p in b["paths"]:
            keys = []
            seen = collections.Counter()
            for d, st in p[2]:
                pref = tuple(p[0][:d])
                seen[(pref, d)] += 1
                keys.append((pref, d, seen[(pref, d)]))
            keyed.append(keys)
        for p, keys in zip(b["paths"], keyed):
            live = set(live_in.get(p[3][1], ())) if p[3][0] == "goto" else (set(allregs) if p[3][0] != "ret" else set())
            if p[3][0] != "goto":
                _regs_in(p[3], live)
            conds = p[1]
            pending = len(conds) - 1
            for (d, st), key in zip(reversed(p[2]), reversed(keys)):
                while pending >= 0 and pending >= d:
                    _regs_in(conds[pending], live)
                    pending -= 1
                if st[0] == "set" and isinstance(st[1], tuple) and st[1][0] == "r":
                    i = st[1][1]
                    dead = i not in live and i not in captured and not _has_any(st[2], _IMPURE)
                    dead_votes[key].append(dead)
                    # a removed statement neither defines nor reads anything; keep the analysis as if it
                    # stays when another path may still need it
                    if not dead:
                        live.discard(i)
                        _regs_in(st[2], live)
                else:
                    _regs_in(st, live)
        drop = {k for k, v in dead_votes.items() if v and all(v)}
        if not drop:
            continue
        for p, keys in zip(b["paths"], keyed):
            keep = [(d, st) for (d, st), key in zip(p[2], keys) if key not in drop]
            if len(keep) != len(p[2]):
                removed += len(p[2]) - len(keep)
                p[2][:] = keep
    return removed


def bypass_trampolines(blocks):
    """a block that does nothing (one path, no condition, no IR) and jumps on: point its predecessors at
    the target. Luraph's first-run resolvers leave such blocks behind once their slot writes are folded;
    variants that only differ by passing through one become identical."""
    def is_tramp(bid):
        b = blocks.get(bid)
        if b is None or bid == 0 or len(b["paths"]) != 1:
            return False
        p = b["paths"][0]
        return not p[0] and not p[1] and not p[2] and p[3][0] == "goto" and p[3][1] != bid

    def final(bid):
        seen = set()
        while is_tramp(bid) and bid not in seen:
            seen.add(bid)
            bid = blocks[bid]["paths"][0][3][1]
        return bid
    n = 0
    for bid, b in blocks.items():
        for p in b["paths"]:
            if p[3][0] == "goto":
                t = final(p[3][1])
                if t != p[3][1]:
                    p[3] = ("goto", t)
                    n += 1
    return n


def _norm(x, bid):
    """rename this block's temporaries / call ids to a block-independent form"""
    if isinstance(x, tuple):
        if len(x) == 3 and x[0] == "t" and x[1] == bid:
            return ("t", "B", x[2])
        if len(x) == 2 and x[0] == bid and isinstance(x[1], int) and not isinstance(x[0], str):
            return ("B", x[1])
        return tuple(_norm(y, bid) for y in x)
    if isinstance(x, list):
        return tuple(_norm(y, bid) for y in x)
    return x


def merge_variants(blocks):
    """blocks: bid -> {"pc", "paths": [(taken, conds, ir, outcome)]}; returns (new_blocks, merged_count)"""
    # paths must be mutable lists for the dead-spill pass
    blocks = {bid: {"pc": b["pc"], "paths": [[list(p[0]), list(p[1]), list(p[2]), p[3]] for p in b["paths"]]}
              for bid, b in blocks.items()}
    drop_dead_spills(blocks)
    drop_dead_regs(blocks)
    drop_dead_spills(blocks)
    bypass_trampolines(blocks)
    by_pc = collections.Counter(b["pc"] for b in blocks.values())
    if all(c == 1 for c in by_pc.values()):
        return blocks, 0
    local = {}
    for bid, b in blocks.items():
        sig = []
        for p in b["paths"]:
            out = p[3]
            o = ("goto",) if out[0] == "goto" else _norm(out, bid)
            sig.append((tuple(p[0]), _norm(p[1], bid), _norm(p[2], bid), o))
        local[bid] = (b["pc"], tuple(sig))
    # initial partition by local signature
    cls = {}
    ids = {}
    for bid in blocks:
        key = local[bid] if bid != 0 else ("entry",)
        cls[bid] = ids.setdefault(key, len(ids))
    while True:
        ids2 = {}
        new = {}
        for bid, b in blocks.items():
            succ = tuple(cls[p[3][1]] if p[3][0] == "goto" else None for p in b["paths"])
            key = (cls[bid], succ)
            new[bid] = ids2.setdefault(key, len(ids2))
        if len(ids2) == len(ids):
            cls = new
            break
        cls, ids = new, ids2
    rep = {}
    for bid in sorted(blocks):
        rep.setdefault(cls[bid], bid)
    out = {}
    merged = 0
    for bid, b in blocks.items():
        if rep[cls[bid]] != bid:
            merged += 1
            continue
        paths = []
        for p in b["paths"]:
            o = p[3]
            if o[0] == "goto":
                o = ("goto", rep[cls[o[1]]])
            paths.append((p[0], p[1], p[2], o))
        out[bid] = {"pc": b["pc"], "paths": paths}
    return out, merged
