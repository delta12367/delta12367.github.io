import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    L = Lifter()
    pid = int(sys.argv[1])
    res = L.lift(pid)
    keys = set(float(x) for x in sys.argv[2:])
    for name, kk, v in L.I.static_writes:
        if not keys or kk in keys:
            print(name, kk, repr(v)[:80])
    F = L.F[pid]
    print("tables:", {k: F[k] for k in (3,4,5,7,8,9,10)})
t = threading.Thread(target=main); t.start(); t.join()
