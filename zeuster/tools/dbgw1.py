import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    L = Lifter()
    res = L.lift(1)
    keys = set(float(x) for x in sys.argv[1:])
    c = collections.Counter((n, k, v) for n, k, v in L.I.static_writes if k in keys)
    print(c)
t = threading.Thread(target=main); t.start(); t.join()
