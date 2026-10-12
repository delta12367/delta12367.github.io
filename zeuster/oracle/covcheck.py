"""Compare VM coverage of the real (oracle) run with the lifter's reachable pcs."""
import pickle, sys, collections
cov = collections.defaultdict(set)
for ln in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if ln.startswith("COV "):
        _, pid, pcs = ln.rstrip("\n").split(" ", 2)
        cov[int(pid)] |= set(int(x) for x in pcs.split(",") if x)
res = {}
for p in sys.argv[2:]:
    d = pickle.load(open(p, "rb"))
    for k, v in d["results"].items():
        if v is not None:
            res[k] = v
missing = {}
tot = 0
for pid, pcs in cov.items():
    tot += len(pcs)
    if pid not in res:
        missing[pid] = ("PROTO NOT LIFTED", len(pcs))
        continue
    lifted = set(int(b["pc"]) for b in res[pid].values() if b["pc"] is not None)
    miss = pcs - lifted
    if miss:
        missing[pid] = sorted(miss)
print("protos executed", len(cov), "pcs executed", tot)
print("protos with executed-but-unlifted pcs:", len(missing))
for pid, m in sorted(missing.items(), key=lambda x: -len(x[1]) if isinstance(x[1], list) else 0)[:30]:
    print(pid, m[:20] if isinstance(m, list) else m, "..." if isinstance(m, list) and len(m) > 20 else "")
