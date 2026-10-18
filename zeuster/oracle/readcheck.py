"""Reads that differ between the runs, after pairing the ones a method lookup moved (Luau's NAMECALL
looks a method up after the arguments, the VM's SELF before them).

The traces are compared call by call (the main run, then each fuzz/unit call, as in unitdiff.py):
object ids are numbered per call, the non-read events of a call are aligned, and the reads before
each aligned event are compared. A read missing on one side is paired with the same read up to 8
events later on the other side (a moved lookup); what is left is reported as unpaired.

usage: python3 readcheck.py [o.tr d.tr]
"""
import collections, difflib, re, sys
from unitdiff import HEAD, canon
import norm as norm_mod

LOC = re.compile(r"\./run_(orig|dec)[a-z_]*\.luau:\d+: ")


def blocks(path):
    out = [("<main>", [])]
    for ln in open(path, encoding="utf-8", errors="replace").read().split("\n"):
        ln = LOC.sub("<loc>: ", ln)
        if HEAD.match(ln):
            out.append((ln, []))
        else:
            out[-1][1].append(ln)
    return out


def event_key(ln):
    """an event as norm.py compares it"""
    while "<loc>: <loc>: " in ln:
        ln = ln.replace("<loc>: <loc>: ", "<loc>: ")
    ln = norm_mod.MISSING.sub("attempt to call a nil value", ln)
    return norm_mod.FORERR.sub("<compare/for type error>", ln)


def segs(lines):
    """(event, reads before it) pairs; a run of property sets on one object is one event (the order
    of its fields is not compared, as in norm.py)"""
    out, cur = [], []
    run, run_obj, run_reads = [], None, []

    def flush():
        nonlocal run, run_obj, run_reads
        if run:
            out.append(("SETS " + " | ".join(sorted(run)), run_reads))
        run, run_obj, run_reads = [], None, []
    for ln in lines:
        if ln.startswith("get ") or ln.startswith("rawget "):
            cur.append(ln)
            continue
        ln = event_key(ln)
        m = norm_mod.SET.match(ln)
        if m:
            if run_obj is not None and m.group(1) != run_obj:
                flush()
            run_obj = m.group(1)
            run.append(ln)
            run_reads += cur
            cur = []
            continue
        flush()
        out.append((ln, cur))
        cur = []
    flush()
    out.append(("<end>", cur))
    return out


def main():
    pa, pb = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("o.tr", "d.tr")
    A, B = blocks(pa), blocks(pb)
    moved = collections.Counter()
    unpaired = []
    for (ha, la), (hb, lb) in zip(A, B):
        if ha != hb:
            unpaired.append(("call headers differ", ha, hb))
            continue
        if ha != "<main>":
            la, lb = canon(la), canon(lb)
        sa, sb = segs(la), segs(lb)
        ea, eb = [e for e, _ in sa], [e for e, _ in sb]
        pending_o = collections.defaultdict(list)
        pending_d = collections.defaultdict(list)
        if ea == eb:
            ops = [("equal", 0, len(ea), 0, len(eb))]
        else:
            ops = difflib.SequenceMatcher(None, ea, eb, autojunk=False).get_opcodes()
        pos = 0
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                for k in range(i2 - i1):
                    ca, cb = collections.Counter(sa[i1 + k][1]), collections.Counter(sb[j1 + k][1])
                    for r, c in (ca - cb).items():
                        pending_o[r] += [pos + k] * c
                    for r, c in (cb - ca).items():
                        pending_d[r] += [pos + k] * c
                pos += i2 - i1
            else:
                for _, g in sa[i1:i2]:
                    for r in g:
                        pending_o[r].append(pos)
                for _, g in sb[j1:j2]:
                    for r in g:
                        pending_d[r].append(pos)
                pos += 1
        for r in set(pending_o) | set(pending_d):
            po, pd = pending_o.get(r, []), pending_d.get(r, [])
            while po and pd and 0 <= pd[0] - po[0] <= 8:
                moved[r.split(" -> ")[0].rsplit(".", 1)[-1]] += 1
                po.pop(0)
                pd.pop(0)
            for i in po:
                unpaired.append(("orig only", ha, r))
            for i in pd:
                unpaired.append(("dec only", ha, r))
    print("moved method lookups:", sum(moved.values()), dict(moved.most_common(8)))
    print("unpaired reads:", len(unpaired))
    for u in unpaired[:20]:
        print("  ", u[0], "|", u[1][:40], "|", u[2][:120])


if __name__ == "__main__":
    main()
