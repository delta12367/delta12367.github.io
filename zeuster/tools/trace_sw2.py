import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter
    L = Lifter()
    pid = int(sys.argv[1]); key = float(sys.argv[2])
    orig = lifter2.SymInterp.static_write
    seen = set()
    def sw(self, t, k, v):
        kk = lifter2.key_norm(k)
        if kk == key:
            sig = (t.name, self.cur_pc(), repr(v))
            if sig not in seen:
                seen.add(sig)
                print("WRITE", t.name, kk, v, "at vm pc", self.cur_pc(), "block", self.blockid, flush=True)
        return orig(self, t, k, v)
    lifter2.SymInterp.static_write = sw
    L.lift(pid)
t = threading.Thread(target=main); t.start(); t.join()
