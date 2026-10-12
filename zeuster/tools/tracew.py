import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter, ITable, Registers, uw
    L = Lifter()
    target = float(sys.argv[2])
    seen = collections.Counter()
    orig = lifter2.SymInterp.exec_stmt
    def ex(self, s, sc):
        if self.cur_pc() == target and s.k == "GenFor":
            try:
                W = self.lookup_var("W", sc)
            except Exception:
                W = "?"
            if isinstance(W, ITable):
                desc = []
                for k in sorted(W.h, key=repr):
                    b = W.h[k]
                    if isinstance(b, ITable):
                        desc.append("%s:%s%s" % (int(k), "C" if b.closed else "o", "R" if isinstance(b.h.get(3.0), Registers) else "-"))
                    else:
                        desc.append("%s:%s" % (k, type(b).__name__))
                seen[("W", s.line, " ".join(desc))] += 1
            else:
                seen[("W", s.line, repr(W)[:40])] += 1
        return orig(self, s, sc)
    lifter2.SymInterp.exec_stmt = ex
    L.lift(int(sys.argv[1]))
    for k, c in seen.items():
        print(c, k)
t = threading.Thread(target=main); t.start(); t.join()
