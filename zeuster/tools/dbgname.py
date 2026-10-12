import sys, threading, pickle, os, re
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import assemble, decompile, patterns, natives
    from decompile import Decompiler, FuncPrinter
    want = sys.argv[1]
    captured = []
    orig_init = FuncPrinter.__init__
    def init(self, D, info, upnames, depth, taken):
        orig_init(self, D, info, upnames, depth, taken)
        if info.pid == 1:
            captured.append(self)
    FuncPrinter.__init__ = init
    head_lines = open("head.lua", encoding="utf-8").read().split("\n")
    nat = natives.load(head_lines)
    results = assemble.load_results(["root.pkl", "noroot.pkl"])
    C, count, F, metas = pickle.load(open("progdata.pkl", "rb"))
    native_src = {name: (lambda ns: (lambda upnames, indent, taken: natives.render(ns, upnames, indent)))(ns) for name, ns in nat.items()}
    D = Decompiler(results, lambda pid: int(F[pid][6]), native_src, assemble.hostfn_factory(head_lines), assemble.collect_globals(results))
    D.native_meta = nat
    text = D.function_text(1, [], "", 0, set())
    fp = captured[0]
    inv = {}
    for w, n in fp.names.items():
        inv.setdefault(n, []).append(w)
    ws = inv.get(want)
    print("name", want, "webs", ws)
    for w in ws or []:
        print(" web", w, fp.info.webinfo.get(w))
        for st in patterns.walk_stmts(fp.info.body):
            if st[0] == "ir" and st[1][0] == "set" and st[1][1] == ("w", w):
                print("   def:", repr(st[1][2])[:130])
            if st[0] == "local" and w in st[1]:
                print("   local:", repr(st[2])[:130])
t = threading.Thread(target=main); t.start(); t.join()
