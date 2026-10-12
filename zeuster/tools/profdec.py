import sys, pickle, threading, time
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    from ir import build_func
    from opt import Optimizer
    from webs import compute_webs
    from structure import Structurer, cleanup
    import decompile
    data = pickle.load(open(sys.argv[1], "rb"))
    pid = int(sys.argv[2])
    blocks = data["results"][pid]
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    t = time.time()
    F = build_func(blocks)
    print("build", time.time() - t, "bbs", len(F.bbs), "stmts", sum(len(b.stmts) for b in F.bbs.values()), flush=True)
    n = decompile.detect_params(F)
    t = time.time()
    O = Optimizer(F, int(F_[pid][6]))
    O.run()
    print("opt", time.time() - t, "stmts", sum(len(b.stmts) for b in F.bbs.values()), flush=True)
    import tailmerge
    from structure import merge_conditions
    t = time.time()
    print("tailmerge", tailmerge.run(F), time.time() - t, flush=True)
    t = time.time()
    print("mergecond", merge_conditions(F), time.time() - t, flush=True)
    t = time.time()
    wi = compute_webs(F, n)
    print("webs", time.time() - t, len(wi), flush=True)
    t = time.time()
    S = Structurer(F)
    print("struct init", time.time() - t, "loops", len(S.loops), flush=True)
    t = time.time()
    body = S.run()
    print("struct run", time.time() - t, S.problems[:5], flush=True)
    import patterns
    from scope import place_locals
    t = time.time()
    body = cleanup(body)
    print("cleanup", time.time() - t, flush=True)
    body = patterns.apply(body, wi)
    print("patterns", time.time() - t, flush=True)
    body = place_locals(body, wi)
    print("locals", time.time() - t, flush=True)
    body = patterns.post(body)
    print("post", time.time() - t, flush=True)


t = threading.Thread(target=main)
t.start()
t.join()
