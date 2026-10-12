"""Compare the reads between consecutive non-read events (the non-read sequences must match)."""
import sys, collections, re
loc = re.compile(r"\./run_(orig|dec)[a-z_]*\.luau:\d+: ")
def segs(path):
    out = []
    cur = []
    for ln in open(path, errors="replace").read().split("\n"):
        ln = loc.sub("<loc>: ", ln)
        if ln.startswith("get ") or ln.startswith("rawget "):
            cur.append(ln)
        else:
            out.append((ln, cur))
            cur = []
    return out
a, b = segs(sys.argv[1]), segs(sys.argv[2])
print(len(a), len(b))
n = 0
for i, ((ea, ga), (eb, gb)) in enumerate(zip(a, b)):
    if ea != eb:
        print("event mismatch at", i, ea[:100], "|", eb[:100])
        break
    if collections.Counter(ga) != collections.Counter(gb):
        n += 1
        if n <= 5:
            print("segment", i, "before:", ea[:100])
            print("   orig only:", list((collections.Counter(ga) - collections.Counter(gb)).items())[:5])
            print("   dec only: ", list((collections.Counter(gb) - collections.Counter(ga)).items())[:5])
print("segments with different reads:", n)
