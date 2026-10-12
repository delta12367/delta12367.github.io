import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter, ITable, uw
    L = Lifter()
    lines = {24297, 25264, 27262}
    seen = collections.Counter()
    orig = lifter2.SymInterp.exec_stmt
    def ex(self, s, sc):
        if s.k == "GenFor" and getattr(s, "line", None) in lines:
            try:
                W = self.lookup_var("W", sc)
            except Exception:
                W = None
            keys = sorted(int(k) for k in W.h) if isinstance(W, ITable) else repr(W)[:30]
            seen[(self.cur_pc(), s.line, str(keys))] += 1
        return orig(self, s, sc)
    lifter2.SymInterp.exec_stmt = ex
    L.lift(int(sys.argv[1]))
    for k, c in sorted(seen.items(), key=repr):
        if sys.argv[2] in k[2] or sys.argv[2] == "all":
            print(k, c)
    print("total close executions", sum(seen.values()), "distinct", len(seen))
t = threading.Thread(target=main); t.start(); t.join()
