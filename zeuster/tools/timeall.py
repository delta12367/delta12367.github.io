import sys, pickle, threading, time, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    import decompile
    data = pickle.load(open(sys.argv[1], "rb"))
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    res = data["results"]
    pids = sorted((p for p in res if res[p]), key=lambda p: len(F_[F_[p][3].i]))
    probs = collections.Counter()
    for pid in pids:
        print("start", pid, flush=True)
        t = time.time()
        try:
            info = decompile.analyse(pid, res[pid], int(F_[pid][6]))
            for l in info.log:
                if not l.startswith("spill") and not l.startswith("tail"):
                    probs[l.split(" ")[0] + " " + (l.split(" ")[1] if len(l.split(" ")) > 1 else "")] += 1
        except Exception as e:
            print("FAIL", pid, repr(e)[:200], flush=True)
        dt = time.time() - t
        if dt > 2:
            print("slow", pid, len(F_[F_[pid][3].i]), "%.1f" % dt, flush=True)
    print(probs.most_common(20))


t = threading.Thread(target=main)
t.start()
t.join()
