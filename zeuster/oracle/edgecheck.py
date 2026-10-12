"""Executed VM edges (oracle run) that the lifted CFG does not contain (generic template only)."""
import sys as _s; _s.path.insert(0, "../work")
import pickle, sys, collections
C, count, F, metas = pickle.load(open("../work/progdata.pkl", "rb"))
edges = collections.defaultdict(set)
for ln in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if ln.startswith("COVE "):
        parts = ln.rstrip("\n").split(" ", 2)
        pid = int(parts[1])
        if len(parts) > 2 and parts[2]:
            for e in parts[2].split(","):
                a, b = e.split(">")
                edges[pid].add((int(a), int(b)))
res = {}
for p in sys.argv[2:]:
    d = pickle.load(open(p, "rb"))
    for k, v in d["results"].items():
        if v is not None:
            res[k] = v
bad = {}
for pid, es in edges.items():
    if F[pid][1] != 165 or pid not in res:
        continue
    blocks = res[pid]
    lifted = set()
    for b in blocks.values():
        for p in b["paths"]:
            if p[3][0] == "goto" and b["pc"] is not None:
                lifted.add((int(b["pc"]), int(blocks[p[3][1]]["pc"])))
    miss = sorted(e for e in es if e not in lifted)
    if miss:
        bad[pid] = miss
print("protos with executed edges missing from lift:", len(bad))
for pid, m in sorted(bad.items(), key=lambda x: -len(x[1])):
    print(pid, len(m), m[:15])
