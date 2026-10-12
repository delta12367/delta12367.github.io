import sys, time, collections, threading, pickle
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)

def main():
    from lifter2 import Lifter
    from batch_lift import strip_state
    L = Lifter()
    t0 = time.time()
    res = L.lift(1, verbose=True)
    blocks = res["blocks"]
    print("lifted", len(blocks), "blocks in", time.time() - t0, "processed", res["nproc"], flush=True)
    print(L.I.warnings)
    errs = collections.Counter()
    for b in blocks.values():
        for p in b["paths"]:
            if p[3][0] in ("err", "lifterr", "tamper"):
                errs[p[3][0] + " " + p[3][1][:200]] += 1
    for e, c in errs.most_common(30):
        print(c, e)
    pickle.dump({"results": {1: strip_state(blocks)}}, open("root.pkl", "wb"))

t = threading.Thread(target=main)
t.start()
t.join()
