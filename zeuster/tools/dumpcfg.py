import sys, pickle, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def dump(F, title):
    print("=====", title, "entry", F.entry)
    from structure import rpo
    for n in rpo(F):
        b = F.bbs[n]
        print("BB%d preds=%s pcs=%s" % (n, sorted(b.preds), b.pcs[:4]))
        for s in b.stmts:
            print("    ", repr(s)[:260])
        print("   ->", repr(b.term)[:260])


def main():
    from ir import build_func
    from opt import Optimizer
    import decompile
    src = sys.argv[1]
    pid = int(sys.argv[2])
    stage = sys.argv[3] if len(sys.argv) > 3 else "raw"
    data = pickle.load(open(src, "rb"))
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    F = build_func(data["results"][pid])
    if stage == "raw":
        dump(F, "raw")
        return
    nparams = decompile.detect_params(F)
    log = []
    O = Optimizer(F, int(F_[pid][6]), log)
    O.prepare()
    if stage == "prep":
        dump(F, "prepared")
        return
    from webs import compute_webs
    webinfo = compute_webs(F, nparams)
    O.inline_loop()
    import tailmerge
    tailmerge.run(F)
    from structure import merge_conditions
    merge_conditions(F)
    decompile.fold_param_copies(F, webinfo)
    dump(F, "final cfg")


t = threading.Thread(target=main)
t.start()
t.join()
