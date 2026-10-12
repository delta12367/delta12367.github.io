import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter, Registers
    L = Lifter()
    pid = int(sys.argv[1]); lo = float(sys.argv[2]); hi = float(sys.argv[3])
    orig = lifter2.SymInterp.setindex
    seen = set()
    def si(self, obj, key, val):
        o = obj.v if isinstance(obj, lifter2.KVal) else obj
        pc = self.cur_pc()
        if isinstance(o, Registers) and pc is not None and lo <= pc <= hi:
            sig = (pc, repr(key), repr(val)[:120])
            if sig not in seen:
                seen.add(sig)
                print("pc", pc, "R[%r] =" % (key,), repr(val)[:120], flush=True)
        return orig(self, obj, key, val)
    lifter2.SymInterp.setindex = si
    L.lift(pid)
t = threading.Thread(target=main); t.start(); t.join()
