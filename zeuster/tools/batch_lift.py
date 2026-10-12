import sys, time, collections, threading, pickle, traceback
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def strip_state(blocks):
    """drop interpreter state objects so results can be pickled"""
    out = {}
    for bid, b in blocks.items():
        paths = []
        for p in b["paths"]:
            paths.append((p[0], p[1], p[2], p[3]))
        out[bid] = {"pc": b["pc"], "paths": paths}
    return out


def main():
    from lifter2 import Lifter, LiftError
    L = Lifter()
    stubs = {n for n, t in L.protos.items() if len(L.F[t[3].i]) == 1 and L.F[t[3].i].get(1) == 4503599627370495}
    todo = [n for n in L.protos if n not in stubs]
    only = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else None
    skip_root = sys.argv[1] == "noroot"
    if only:
        todo = only
    elif skip_root:
        todo = [n for n in todo if n != 1]
    todo.sort(key=lambda n: len(L.F[L.protos[n][3].i]))
    results = {}
    stats = collections.Counter()
    errs = collections.Counter()
    t_all = time.time()
    for i, n in enumerate(todo):
        t0 = time.time()
        def count_err(blocks):
            c = 0
            for b in blocks.values():
                for p in b["paths"]:
                    if p[3][0] in ("err", "lifterr"):
                        c += 1
            return c
        try:
            res = L.lift_best(n)
            blocks = res["blocks"]
            if res.get("data_keys"):
                stats["dedup"] += 1
            nerr = 0
            for b in blocks.values():
                for p in b["paths"]:
                    if p[3][0] in ("err", "lifterr"):
                        nerr += 1
                        errs[p[3][1][:160]] += 1
            results[n] = strip_state(blocks)
            stats["ok" if nerr == 0 else "partial"] += 1
        except Exception as e:
            stats["fail"] += 1
            errs["FAIL " + type(e).__name__ + ": " + str(e)[:150]] += 1
            results[n] = None
        dt = time.time() - t0
        if dt > 20 or i % 50 == 0:
            print(i, n, len(L.F[L.protos[n][3].i]), "%.1fs" % dt, dict(stats), flush=True)
    print("total", time.time() - t_all, dict(stats))
    for e, c in errs.most_common(60):
        print(c, e)
    out = sys.argv[1] + ".pkl"
    with open(out, "wb") as f:
        pickle.dump({"results": results, "static_writes": L.I.static_writes}, f)


if __name__ == "__main__":
    t = threading.Thread(target=main)
    t.start()
    t.join()
