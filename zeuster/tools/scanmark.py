"""Decompile every proto standalone and report which ones contain decompiler markers (own body only)."""
import sys, pickle, threading, re, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)

MARKS = {
    "forin": "decompiler: for-in",
    "forvar": "--[[forvar",
    "forok": "--[[forok]]",
    "regs": "--[[registers]]",
    "unres": "decompiler: unresolved",
    "tamper": "tamper detected",
    "goto": "unstructured jump",
    "q": "--[[?",
    "multi": "--[[multi",
    "sel": "--[[sel",
    "count": "--[[count]]",
    "exitflag": "exitLoop",
}


def main():
    from decompile import Decompiler
    srcs = ["root.pkl", "noroot.pkl"]
    results = {}
    for p in srcs:
        d = pickle.load(open(p, "rb"))
        for k, v in d["results"].items():
            if v is not None:
                results[k] = v
    C, count, F, metas = pickle.load(open("progdata.pkl", "rb"))
    D = Decompiler(results, lambda pid: int(F[pid][6]), {}, lambda e: "--[[hostfn]]nil")
    # stub nested closures so each text is the proto's own body
    D.function_text_orig = D.function_text
    only = [int(x) for x in sys.argv[1:]] or sorted(results)
    tot = collections.Counter()
    per = collections.defaultdict(list)
    for pid in only:
        info = D.info(pid)
        if info is None:
            per["FAILED"].append(pid)
            continue
        from decompile import FuncPrinter
        fp = FuncPrinter(D, info, ["U%d" % i for i in range(60)], 0, set())
        fp.closure = lambda e, indent: "function() --[[proto %d]] end" % e[1]
        fp.native = lambda e, indent: "NATIVE_%s" % e[1]
        lines = fp.text("")
        txt = "\n".join(lines)
        for k, m in MARKS.items():
            c = txt.count(m)
            if c:
                tot[k] += c
                per[k].append((pid, c))
        if re.search(r"\br\d+\b", txt):
            per["rref"].append(pid)
    print(dict(tot))
    for k, v in per.items():
        print(k, len(v), v[:40])


t = threading.Thread(target=main)
t.start()
t.join()
