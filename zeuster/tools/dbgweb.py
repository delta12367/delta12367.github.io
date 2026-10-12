import sys, pickle, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import decompile
    from decompile import Decompiler, FuncPrinter
    data = pickle.load(open(sys.argv[1], "rb"))
    pid = int(sys.argv[2]); want = sys.argv[3]
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    import bisim
    from ir import build_func
    from opt import Optimizer
    blocks, n = bisim.merge_variants(data["results"][pid])
    F = build_func(blocks)
    nparams = decompile.detect_params(F)
    O = Optimizer(F, int(F_[pid][6]), [])
    O.prepare()
    # find captures (rref) and closes per register
    caps = {}
    closes = {}
    for b in F.bbs.values():
        for idx, s in enumerate(b.stmts):
            if s[0] == "close":
                closes.setdefault(s[1], []).append((b.id, idx, b.pcs[:2]))
            from opt import stmt_exprs_any
            from ir import walk
            for e in stmt_exprs_any(s):
                for x in walk(e):
                    if x[0] in ("closure", "native"):
                        for r in x[2]:
                            if r[0] == "rref":
                                caps.setdefault(r[1], []).append((b.id, idx, x[1], b.pcs[:2]))
    D = Decompiler({pid: data["results"][pid]}, lambda p: int(F_[p][6]), {}, lambda e: "nil")
    info = D.info(pid)
    fp = FuncPrinter(D, info, ["U%d" % i for i in range(80)], 0, set())
    inv = {v: k for k, v in fp.names.items()}
    w = inv.get(want)
    print("web", w, "info", info.webinfo.get(w))
    reg = info.webinfo.get(w, {}).get("var")
    if reg and reg[0] == "r":
        L = reg[1]
        print("captures of r%d:" % L, caps.get(L, [])[:10])
        print("closes of r%d:" % L, closes.get(L, [])[:10])

def main2():
    import decompile, patterns
    from decompile import Decompiler, FuncPrinter
    data = pickle.load(open(sys.argv[1], "rb"))
    pid = int(sys.argv[2]); want = sys.argv[3]
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    D = Decompiler({pid: data["results"][pid]}, lambda p: int(F_[p][6]), {}, lambda e: "nil")
    info = D.info(pid)
    fp = FuncPrinter(D, info, ["U%d" % i for i in range(80)], 0, set())
    inv = {v: k for k, v in fp.names.items()}
    w = inv.get(want)
    print("web", w, info.webinfo.get(w))
    for st in patterns.walk_stmts(info.body):
        if st[0] == "ir" and st[1][0] == "set" and st[1][1] == ("w", w):
            print("  def:", repr(st[1][2])[:120])
        if st[0] == "local" and w in st[1]:
            print("  local:", repr(st[2])[:120])
t = threading.Thread(target=main2 if "--defs" in sys.argv else main); t.start(); t.join()
