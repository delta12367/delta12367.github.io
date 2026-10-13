"""share of the emitted functions / lifted VM instructions that the oracle run executed
(the encrypted-constant closures that constfold.py replaces by their value are left out)"""
import sys as _s; _s.path.insert(0, "../work")
import pickle, sys, constfold
res = {}
for p in sys.argv[2:]:
    res.update({k: v for k, v in pickle.load(open(p, "rb"))["results"].items() if v})
folded = constfold.find_eligible(res)
cov = {}
for ln in open(sys.argv[1], errors="replace"):
    if ln.startswith("COV "):
        p = ln.split()
        cov[int(p[1])] = set(int(x) for x in p[2].split(",")) if len(p) > 2 else set()
tot = hit = nexec = nf = 0
for pid, b in res.items():
    if pid in folded:
        continue
    nf += 1
    pcs = set(int(x["pc"]) for x in b.values() if x["pc"] is not None)
    whole = not pcs
    n = 1 if whole else len(pcs)
    tot += n
    if pid in cov:
        nexec += 1
        hit += 1 if whole else len(pcs & cov[pid])
print("functions executed %d / %d, lifted instructions executed %d / %d (%.1f%%)" % (nexec, nf, hit, tot, 100.0 * hit / tot))
