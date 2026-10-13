"""Reads that differ between the runs, after pairing the ones a method lookup moved (Luau's NAMECALL
looks a method up after the arguments, the VM's SELF before them)."""
import collections, re
exec(open("segdiff.py").read().split("a, b = segs")[0])
a, b = segs("o.tr"), segs("d.tr")
moved = collections.Counter()
unpaired = []
pending_o = collections.defaultdict(list)   # read -> segment indexes where only the original has it
pending_d = collections.defaultdict(list)
for i, ((ea, ga), (eb, gb)) in enumerate(zip(a, b)):
    ca, cb = collections.Counter(ga), collections.Counter(gb)
    for r, c in (ca - cb).items():
        pending_o[r] += [i] * c
    for r, c in (cb - ca).items():
        pending_d[r] += [i] * c
for r in set(pending_o) | set(pending_d):
    po, pd = pending_o.get(r, []), pending_d.get(r, [])
    while po and pd and 0 <= pd[0] - po[0] <= 8:
        moved[r.rsplit(".", 1)[-1]] += 1
        po.pop(0)
        pd.pop(0)
    for i in po:
        unpaired.append(("orig only", i, r))
    for i in pd:
        unpaired.append(("dec only", i, r))
print("moved method lookups:", sum(moved.values()), dict(moved.most_common(8)))
print("unpaired reads:", len(unpaired))
for u in sorted(unpaired, key=lambda x: x[1])[:12]:
    print("  ", u[0], u[1], u[2][:100], "| before:", a[u[1]][0][:80])
