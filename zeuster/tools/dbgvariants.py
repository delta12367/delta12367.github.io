import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    from lifter2 import Lifter, sig2
    L = Lifter()
    pid = int(sys.argv[1])
    res = L.lift(pid, verbose=True)
    blocks = res["blocks"]
    bypc = collections.defaultdict(list)
    for bid, b in blocks.items():
        if b["pc"] is not None:
            bypc[b["pc"]].append(bid)
    multi = sorted(((len(v), pc) for pc, v in bypc.items() if len(v) > 1), reverse=True)
    print("pcs with variants:", multi[:15])
    for n, pc in multi[:3]:
        bids = bypc[pc]
        states = [blocks[b]["state"] for b in bids]
        keys = set()
        for st in states:
            keys |= set(st)
        diffs = []
        for k in keys:
            sigs = set(repr(sig2(st.get(k))) for st in states)
            if len(sigs) > 1:
                diffs.append((k, sigs))
        print("pc", pc, "variants", len(bids))
        for k, sg in diffs[:10]:
            print("   ", k, list(sg)[:4])
        print("   reads", [sorted(map(str, blocks[b]["reads"]))[:12] for b in bids[:2]])


t = threading.Thread(target=main)
t.start()
t.join()
