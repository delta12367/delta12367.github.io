"""Append the next fuzz round to fuzz_pids.txt: functions not executed so far that both runs created
equally often (by the end of the last round). Run after ./run.sh."""
import sys as _s; _s.path.insert(0, "../work")
import os, pickle, constfold
res = {}
for p in ("../work/root.pkl", "../work/noroot.pkl"):
    res.update({k: v for k, v in pickle.load(open(p, "rb"))["results"].items() if v})
folded = constfold.find_eligible(res)
rounds = [ln.split() for ln in open("fuzz_pids.txt")] if os.path.exists("fuzz_pids.txt") else []
done = {int(x) for r in rounds for x in r}
last = len(rounds)
cov = set()
regs = {}
for path, side in (("orig.txt", "o"), ("dec.txt", "d")):
    for ln in open(path, errors="replace"):
        if side == "o" and ln.startswith("COV "):
            cov.add(int(ln.split()[1]))
        if ln.startswith("COVREG "):
            _, r, pid, n = ln.split()
            if int(r) == last:
                regs.setdefault(int(pid), {})[side] = int(n)
ids = sorted(p for p, c in regs.items() if c.get("o") == c.get("d") and p not in cov and p not in folded
             and p != 1 and p not in done)
differ = sorted(p for p, c in regs.items() if c.get("o") != c.get("d"))
if ids:
    with open("fuzz_pids.txt", "a") as f:
        f.write(" ".join(map(str, ids)) + "\n")
print("round", last + 1, ":", len(ids), "functions; registry differs for", len(differ), differ[:20])
