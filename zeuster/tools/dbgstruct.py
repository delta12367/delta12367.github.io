import sys, pickle, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    from ir import build_func
    from opt import Optimizer
    from structure import Structurer, merge_conditions
    import decompile, tailmerge
    data = pickle.load(open(sys.argv[1], "rb"))
    pid = int(sys.argv[2])
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    F = build_func(data["results"][pid])
    decompile.detect_params(F)
    Optimizer(F, int(F_[pid][6])).run()
    tailmerge.run(F)
    merge_conditions(F)
    S = Structurer(F)
    for h, body in sorted(S.loops.items(), key=lambda x: len(x[1])):
        print("loop", h, "size", len(body), "follow", S.follow.get(h), "pcs", F.bbs[h].pcs[:3], "body", sorted(body))
    for n in [int(x) for x in sys.argv[3:]]:
        b = F.bbs.get(n)
        if b is None:
            print(n, "missing")
            continue
        print("node", n, "preds", sorted(b.preds), "term", repr(b.term)[:200], "nstmts", len(b.stmts), "pcs", b.pcs[:6])
        inloops = [h for h, body in S.loops.items() if n in body]
        print("   in loops", inloops)
    body = S.run()
    print(S.problems[:10])


t = threading.Thread(target=main)
t.start()
t.join()
