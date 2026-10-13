"""Executed VM code missing from the lift, minus first-run resolver regions.

A region (connected executed pcs that the lifter never produced a block for) is a resolver
region when every edge into it starts at a pc whose operand slots the run patched. Those are
Luraph's first-run jump resolvers: they patch the slot and continue at the target the lifter
already uses. Everything else is reported.
usage: gapcheck.py orig.txt root.pkl noroot.pkl
"""
import sys as _s; _s.path.insert(0, "../work")
import pickle, sys, collections
C, count, F, metas = pickle.load(open("../work/progdata.pkl", "rb"))
cov = {}
edges = collections.defaultdict(set)
patched = collections.defaultdict(set)
for ln in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if ln.startswith("COV "):
        p = ln.split()
        cov[int(p[1])] = set(int(x) for x in p[2].split(",")) if len(p) > 2 else set()
    elif ln.startswith("COVE "):
        p = ln.rstrip("\n").split(" ", 2)
        if len(p) > 2 and p[2]:
            for e in p[2].split(","):
                a, b = e.split(">")
                edges[int(p[1])].add((int(a), int(b)))
    elif ln.startswith("PATCH "):
        _, pid, slot, key, val = ln.split()
        if key.startswith("n:"):
            patched[int(pid)].add(int(float(key[2:])))
res = {}
for path in sys.argv[2:]:
    d = pickle.load(open(path, "rb"))
    for k, v in d["results"].items():
        if v is not None:
            res[k] = v
report = []
ALL = True
import constfold
FOLDED = constfold.find_eligible(res)   # replaced by their value in the output, bodies never emitted
for pid, pcs in cov.items():
    if pid not in res or (F[pid][1] != 165 and not ALL) or pid in FOLDED:
        continue
    if all(b["pc"] is None for b in res[pid].values()):
        continue      # straight-line specialised template: lifted as one block
    lifted = set(int(b["pc"]) for b in res[pid].values() if b["pc"] is not None)
    miss = pcs - lifted
    if not miss:
        continue
    succ = collections.defaultdict(set)
    pred = collections.defaultdict(set)
    for a, b in edges[pid]:
        succ[a].add(b)
        pred[b].add(a)
    seen = set()
    for start in sorted(miss):
        if start in seen:
            continue
        comp = set()
        stack = [start]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            for y in succ[x] | pred[x]:
                if y in miss and y not in comp:
                    stack.append(y)
        seen |= comp
        entries = [(a, b) for b in comp for a in pred[b] if a not in comp]
        resolver = entries and all(a in patched[pid] for a, b in entries)
        if not resolver:
            report.append((pid, sorted(comp), entries))
print("unexplained regions:", len(report))
for pid, comp, entries in report[:60]:
    print(pid, "pcs", comp[:20], ("(+%d)" % (len(comp) - 20)) if len(comp) > 20 else "", "entries", entries[:6])
