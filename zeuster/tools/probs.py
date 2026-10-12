import sys, pickle, threading, time, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def ast_size(block):
    n = 0
    for s in block:
        n += 1
        k = s[0]
        if k == "if":
            n += ast_size(s[2]) + ast_size(s[3])
        elif k == "while":
            n += ast_size(s[2])
        elif k == "repeat":
            n += ast_size(s[1])
        elif k in ("fornum", "forin"):
            n += ast_size(s[5])
    return n


def main():
    import decompile
    res = {}
    for p in sys.argv[1:]:
        res.update(pickle.load(open(p, "rb"))["results"])
    C, count, F_, metas = pickle.load(open("progdata.pkl", "rb"))
    rows = []
    for pid, blocks in res.items():
        if not blocks:
            continue
        t = time.time()
        try:
            info = decompile.analyse(pid, blocks, int(F_[pid][6]))
        except Exception as e:
            rows.append((0, pid, 0, "FAIL " + repr(e)[:100]))
            continue
        probs = [l for l in info.log if not l.startswith(("spill", "tail"))]
        n = ast_size(info.body)
        ninstr = len(F_[F_[pid][3].i])
        rows.append((n / max(1, ninstr), pid, n, ninstr, time.time() - t, collections.Counter(p.split(" ")[0] for p in probs)))
    rows.sort(key=lambda r: -r[0])
    for r in rows[:30]:
        print(r)


t = threading.Thread(target=main)
t.start()
t.join()
