import sys, threading, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    L = Lifter()
    pid = int(sys.argv[1])
    res = L.lift(pid)
    c = collections.Counter((n, k, v) for n, k, v in L.I.static_writes)
    for (n, k, v), cnt in sorted(c.items(), key=lambda x: (x[0][0], x[0][1])):
        print(n, k, v, cnt)
    print("journal-style writes?")
t = threading.Thread(target=main); t.start(); t.join()
