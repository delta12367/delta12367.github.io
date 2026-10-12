import sys, threading, pickle, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import emit
    seen = collections.Counter()
    orig = emit.Emitter.call
    def call(self, f, args, indent):
        r = orig(self, f, args, indent)
        if r.startswith("function"):
            seen[f[0]] += 1
            if seen[f[0]] < 2:
                print("callee kind", f[0], repr(f)[:200])
        return r
    emit.Emitter.call = call
    origx = emit.Emitter.ex
    def ex(self, e, indent):
        r = origx(self, e, indent)
        if e[0] not in ("closure", "native", "callx", "callmx", "mcallx", "mcallmx", "hostfn") and r[0].startswith("function"):
            seen["ex:" + e[0]] += 1
            if seen["ex:" + e[0]] < 3:
                print("expr kind", e[0], repr(e)[:300])
        return r
    emit.Emitter.ex = ex
    import assemble
    sys.argv = ["x", "dbg.lua", "root.pkl", "noroot.pkl"]
    assemble.main()
    print(seen)
t = threading.Thread(target=main); t.start(); t.join()
