import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter, ITable, Registers, uw
    L = Lifter()
    reg = float(sys.argv[2])
    seen = collections.Counter()
    orig_si = lifter2.SymInterp.setindex
    def si(self, obj, key, val):
        o = obj.v if isinstance(obj, lifter2.KVal) else obj
        if isinstance(o, ITable) and isinstance(o.h.get(3.0), Registers) and uw(o.h.get(2.0)) == reg:
            k = uw(key)
            if k in (1.0, 2.0, 3.0):
                seen[("boxwrite", self.cur_pc(), k, "closed" if o.closed else "open")] += 1
        return orig_si(self, obj, key, val)
    lifter2.SymInterp.setindex = si
    orig_rs = lifter2.SymInterp.rawset
    def rs(self, t, k, v):
        r = orig_rs(self, t, k, v)
        if isinstance(t, ITable) and isinstance(t.h.get(3.0), Registers) and uw(t.h.get(2.0)) == reg:
            seen[("boxcreate", self.cur_pc())] += 1
        return r
    lifter2.SymInterp.rawset = rs
    res = L.lift(int(sys.argv[1]))
    for k, c in sorted(seen.items(), key=repr):
        print(k, c)
t = threading.Thread(target=main); t.start(); t.join()
