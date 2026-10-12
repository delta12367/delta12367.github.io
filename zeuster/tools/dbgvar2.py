import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter, sig2, compatible, ITable, Registers
    L = Lifter()
    pid = int(sys.argv[1])
    res = L.lift(pid)
    blocks = res["blocks"]
    by = collections.defaultdict(list)
    for bid, b in blocks.items():
        by[b["pc"]].append(bid)
    def flat(v, path, out, depth=0):
        if isinstance(v, ITable) and depth < 6:
            for k2, v2 in v.h.items():
                flat(v2, path + "/" + repr(k2), out, depth + 1)
        else:
            out[path] = v
    for pc, bids in sorted(by.items(), key=lambda x: (x[0] is None, x[0])):
        if len(bids) < 2:
            continue
        states = []
        for bid in bids:
            st = blocks[bid].get("state") or {}
            fl = {}
            for k, v in st.items():
                flat(v, str(k), fl)
            states.append(fl)
        keys = set().union(*states)
        diffs = []
        for k in sorted(keys):
            sigs = [sig2(s.get(k)) for s in states]
            if any(not compatible(sigs[0], x) for x in sigs[1:]):
                diffs.append((k, [repr(s.get(k))[:40] for s in states]))
        print("pc", pc, "variants", bids)
        for d in diffs[:8]:
            print("    ", d)
t = threading.Thread(target=main); t.start(); t.join()
