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
