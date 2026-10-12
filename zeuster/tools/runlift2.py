import sys, time, collections, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)

def main():
    from lifter2 import Lifter, LiftError
    t0 = time.time()
    L = Lifter()
    print("setup", time.time() - t0)
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    t0 = time.time()
    res = L.lift(pid, verbose=True)
    blocks = res["blocks"]
    print("lifted", len(blocks), "blocks in", time.time() - t0, "processed", res["nproc"])
    print(L.I.warnings)
    print("static writes", len(L.I.static_writes), "global patches", len(L.I.global_patch), "trampolines", L.I.trampolines)
    errs = collections.Counter()
    for bid, b in blocks.items():
        for p in b["paths"]:
            out = p[3]
            if out[0] in ("err", "lifterr"):
                errs[out[1][:300]] += 1
    for e, c in errs.most_common(30):
        print(c, e)
    if len(sys.argv) > 2:
        for bid in sorted(blocks):
            b = blocks[bid]
            print("== block", bid, "pc", b["pc"], "paths", len(b["paths"]))
            for p in b["paths"]:
                taken, conds, ir, out = p[:4]
                print("  path", taken, [repr(c)[:80] for c in conds])
                for d, st in ir:
                    print("     ", d, repr(st)[:200])
                print("   ->", repr(out)[:200])

t = threading.Thread(target=main)
t.start()
t.join()
