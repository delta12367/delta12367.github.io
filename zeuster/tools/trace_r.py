import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter, Registers
    L = Lifter()
    pid = int(sys.argv[1]); reg = float(sys.argv[2])
    orig = lifter2.SymInterp.setindex
    seen = set()
    def si(self, obj, key, val):
        o = obj.v if isinstance(obj, lifter2.KVal) else obj
        k = key.v if isinstance(key, lifter2.KVal) else key
        if isinstance(o, Registers) and k == reg:
            sig = (self.cur_pc(), repr(val)[:80])
            if sig not in seen:
                seen.add(sig)
                print("pc", self.cur_pc(), "block", self.blockid, "R%d =" % reg, repr(val)[:80], flush=True)
        return orig(self, obj, key, val)
    lifter2.SymInterp.setindex = si
    L.lift(pid)
t = threading.Thread(target=main); t.start(); t.join()
