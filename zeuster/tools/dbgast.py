import sys, pickle, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from ir import build_func
    from opt import Optimizer
    from structure import Structurer, cleanup, merge_conditions
    from webs import compute_webs
    import decompile, tailmerge, loops, patterns
    data = pickle.load(open(sys.argv[1], "rb"))
    pid = int(sys.argv[2])
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    F = build_func(data["results"][pid])
    nparams = decompile.detect_params(F)
    log = []
    O = Optimizer(F, int(F_[pid][6]), log)
    O.prepare()
    webinfo = compute_webs(F, nparams)
    O.inline_loop()
    tailmerge.run(F)
    loops.fold_dynamic_fornum(F)
    merge_conditions(F)
    decompile.fold_param_copies(F, webinfo)
    S = Structurer(F)
    body = cleanup(S.run())
    body = patterns.sink_common_tails(body)
    def show(b, ind=""):
        for st in b:
            if st[0] in ("if", "while", "repeat", "fornum", "forin", "do"):
                print(ind + repr(st[:2] if st[0] != "repeat" else st[:1])[:200])
                for cb in patterns.child_blocks(st):
                    show(cb, ind + "    ")
                    print(ind + "  --")
            else:
                print(ind + repr(st)[:200])
    show(body)
t = threading.Thread(target=main); t.start(); t.join()
