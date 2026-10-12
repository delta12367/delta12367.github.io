import sys, threading, collections, pickle
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    from batch_lift import strip_state
    import bisim
    L = Lifter()
    pid = int(sys.argv[1]); keys = set(sys.argv[2].split(","))
    def stats(res):
        blocks = res["blocks"]
        errs = sum(1 for b in blocks.values() for p in b["paths"] if p[3][0] in ("err", "lifterr"))
        by = collections.Counter(b["pc"] for b in blocks.values())
        dups = sum(c - 1 for c in by.values() if c > 1)
        merged, n = bisim.merge_variants(strip_state(blocks))
        by2 = collections.Counter(b["pc"] for b in merged.values())
        return len(blocks), errs, dups, sum(c - 1 for c in by2.values() if c > 1)
    r0 = L.lift(pid)
    print("normal: blocks %d errs %d dups %d dups-after-bisim %d" % stats(r0))
    r1 = L._lift(pid, False, 16, keys)
    print("data %s: blocks %d errs %d dups %d dups-after-bisim %d" % ((sorted(keys),) + stats(r1)))
t = threading.Thread(target=main); t.start(); t.join()
