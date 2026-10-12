import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    L = Lifter()
    res = L.lift(int(sys.argv[1]), verbose=True)
    print("blocks", len(res["blocks"]), "nproc", res["nproc"])
t = threading.Thread(target=main); t.start(); t.join()
