import sys, pickle, threading, time, cProfile, pstats
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import decompile
    data = pickle.load(open(sys.argv[1], "rb"))
    pid = int(sys.argv[2])
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    t = time.time()
    pr = cProfile.Profile()
    pr.enable()
    try:
        info = decompile.analyse(pid, data["results"][pid], int(F_[pid][6]))
    except Exception as e:
        import traceback; traceback.print_exc(limit=8)
    pr.disable()
    print("analyse", time.time() - t)
    pstats.Stats(pr).sort_stats("cumulative").print_stats(18)
t = threading.Thread(target=main); t.start(); t.join()
