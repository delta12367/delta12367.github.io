import sys, pickle, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    from decompile import Decompiler
    data = pickle.load(open(sys.argv[1], "rb"))
    results = data["results"]
    C, count, F, metas = pickle.load(open("progdata.pkl", "rb"))
    import constfold
    allres = dict(results)
    for extra in ("root.pkl", "noroot.pkl"):
        try:
            allres.update({k: v for k, v in pickle.load(open(extra, "rb"))["results"].items() if v})
        except OSError:
            pass
    constfold.ELIGIBLE = constfold.find_eligible(allres)

    def nregs_of(pid):
        return int(F[pid][6])
    D = Decompiler(results, nregs_of, {}, lambda e: "--[[hostfn]]nil")
    for a in sys.argv[2:]:
        pid = int(a)
        txt = D.function_text(pid, ["U%d" % i for i in range(40)], "", 0)
        print("-- proto", pid, D.info(pid).log if D.info(pid) else D.logs.get(pid))
        print("local f = " + txt)
        print()


t = threading.Thread(target=main)
t.start()
t.join()
