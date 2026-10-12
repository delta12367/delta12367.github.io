import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter
    L = Lifter()
    vm = L.state.h
    for k in [float(x) for x in sys.argv[1:]]:
        print(int(k), repr(vm.get(k))[:80])
t = threading.Thread(target=main); t.start(); t.join()
