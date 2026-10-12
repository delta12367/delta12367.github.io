import sys, time, collections
from lifter import Lifter, LiftError
import lift_core
t0 = time.time()
L = Lifter()
print("setup", time.time() - t0); L.I.warnings.clear()
pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
t0 = time.time()
blocks = lift_core.lift(L, pid, verbose=True)
print("lifted", len(blocks), "blocks in", time.time() - t0)
print(L.I.warnings)
errs = collections.Counter()
for bid, b in blocks.items():
    for taken, conds, ir, out, wr in b["paths"]:
        if out[0] in ("err", "lifterr"):
            errs[out[1][:300]] += 1
for e, c in errs.most_common(30):
    print(c, e)
if len(sys.argv) > 2:
    for bid in sorted(blocks):
        b = blocks[bid]
        print("== block", bid, "pc", b["pc"], "paths", len(b["paths"]))
        for taken, conds, ir, out, wr in b["paths"]:
            print("  path", taken, [repr(c)[:80] for c in conds])
            for d, st in ir:
                print("     ", d, repr(st)[:200])
            print("   ->", repr(out)[:200])
