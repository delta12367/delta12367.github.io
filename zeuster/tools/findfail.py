import sys, threading, traceback
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    L = Lifter()
    stubs = {n for n, t in L.protos.items() if len(L.F[t[3].i]) == 1 and L.F[t[3].i].get(1) == 4503599627370495}
    todo = sorted([n for n in L.protos if n not in stubs and n != 1], key=lambda n: len(L.F[L.protos[n][3].i]))
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    shown = 0
    import time
    for n in todo[lo:hi]:
        print("start", n, len(L.F[L.protos[n][3].i]), flush=True)
        t0 = time.time()
        try:
            L.lift(n)
        except Exception as e:
            print("FAIL", n, type(e).__name__, str(e)[:300])
            if shown < 2:
                traceback.print_exc()
                shown += 1
t = threading.Thread(target=main); t.start(); t.join()
