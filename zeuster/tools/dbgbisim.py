import pickle, sys, collections
import bisim
pid = int(sys.argv[2])
r = pickle.load(open(sys.argv[1], 'rb'))['results'][pid]
out, n = bisim.merge_variants(r)
print("merged", n, "remaining blocks", len(out))
by = collections.defaultdict(list)
for bid, b in out.items():
    by[b["pc"]].append(bid)
shown = 0
for pc, bids in sorted(by.items(), key=lambda x: (x[0] is None, x[0])):
    if len(bids) < 2:
        continue
    a, b = bids[0], bids[1]
    pa, pb = out[a]["paths"], out[b]["paths"]
    print("pc", pc, "variants", bids, "npaths", len(pa), len(pb))
    if len(pa) == len(pb):
        for x, y in zip(pa, pb):
            ia = bisim._norm(x[2], a); ib = bisim._norm(y[2], b)
            if x[0] != y[0]:
                print("   taken differs", x[0], y[0]); break
            if bisim._norm(x[1], a) != bisim._norm(y[1], b):
                print("   conds differ", repr(bisim._norm(x[1], a))[:150], "|", repr(bisim._norm(y[1], b))[:150]); break
            if ia != ib:
                for s1, s2 in zip(ia, ib):
                    if s1 != s2:
                        print("   ir differs", repr(s1)[:140], "|", repr(s2)[:140]); break
                else:
                    print("   ir length differs", len(ia), len(ib))
                break
            if x[3][0] == "goto" and y[3][0] == "goto":
                if out[x[3][1]]["pc"] != out[y[3][1]]["pc"]:
                    print("   succ pc differs"); break
                if x[3][1] != y[3][1]:
                    print("   succ differ (variants)", x[3][1], y[3][1], "pc", out[x[3][1]]["pc"]); 
            elif x[3] != y[3]:
                print("   outcome differs", repr(x[3])[:100], repr(y[3])[:100])
    shown += 1
    if shown > 25:
        break
