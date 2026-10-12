import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter
    L = Lifter()
    orig = lifter2.SymInterp.static_write
    seen = collections.Counter()
    def sw(self, t, k, v):
        kk = lifter2.key_norm(k)
        if t.name == "data[3606]" and kk in (146.0, 264.0, 263.0, 262.0):
            seen[(kk, v, self.cur_pc())] += 1
        return orig(self, t, k, v)
    lifter2.SymInterp.static_write = sw
    L.lift(404)
    for k, c in sorted(seen.items(), key=repr):
        print(k, c)
    F = L.F[404]
    jt = L.tables[F[5].i]
    print("orig values", {k: jt.h.get(float(k)) for k in (146, 262, 263, 264)})
t = threading.Thread(target=main); t.start(); t.join()
