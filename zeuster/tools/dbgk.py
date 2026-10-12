import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter, sig2, compatible
    L = Lifter()
    pid = int(sys.argv[1])
    orig = lifter2.lift_once
    shown = [0]
    def hooked(L_, pid_, extra, verbose=False):
        res = orig(L_, pid_, extra, verbose)
        blocks = res["blocks"]
        for (pc, v) in sorted(res["conflicts"], key=repr)[:3]:
            if not isinstance(v, str):
                continue
            for b, blk in blocks.items():
                if blk["pc"] != pc:
                    continue
                rep = blk["state"]
                print("conflict pc", pc, "var", v, "block state:", repr(rep.get(v))[:80])
        return res
    lifter2.lift_once = hooked
    try:
        L.lift(pid)
    except Exception as e:
        print("ERR", e)
t = threading.Thread(target=main); t.start(); t.join()
