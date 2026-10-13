"""Normalise a trace for semantic comparison: drop pure reads, sort runs of property sets on one object."""
import sys, re
SET = re.compile(r"^set (\S+?)\.([^ .]+) = ")
MISSING = re.compile(r"attempt to call missing method '[^']*' of \w+")
# a numeric for over a non-number: the VM compiles the loop to comparisons, Luau checks the operands
FORERR = re.compile(r"invalid 'for' (?:initial value|limit|step) \(number expected, got \w+\)|attempt to compare \w+ (?:<=|<|>=|>) \w+")


def norm(lines):
    out = []
    run = []
    run_obj = None

    def flush():
        nonlocal run, run_obj
        out.extend(sorted(run))
        run = []
        run_obj = None
    loc = re.compile(r"\./run_(orig|dec)[a-z_]*\.luau:\d+: ")
    for ln in lines:
        if ln.startswith("get ") or ln.startswith("rawget "):
            continue
        ln = loc.sub("<loc>: ", ln)
        while "<loc>: <loc>: " in ln:      # the VM re-raises errors with a second position prefix
            ln = ln.replace("<loc>: <loc>: ", "<loc>: ")
        # obj:m() on a value without m: the VM looks m up first (SELF) and calls nil, Luau's NAMECALL
        # reports the missing method by name
        ln = MISSING.sub("attempt to call a nil value", ln)
        ln = FORERR.sub("<compare/for type error>", ln)
        m = SET.match(ln)
        if m:
            if run_obj is not None and m.group(1) != run_obj:
                flush()
            run_obj = m.group(1)
            run.append(ln)
            continue
        flush()
        out.append(ln)
    flush()
    return out


if __name__ == "__main__":
    a = norm(open(sys.argv[1], encoding="utf-8", errors="replace").read().split("\n"))
    b = norm(open(sys.argv[2], encoding="utf-8", errors="replace").read().split("\n"))
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            print("first divergence at normalized event", i, "of", len(a), len(b))
            for j in range(max(0, i - 12), min(n, i + 6)):
                mark = "  " if a[j] == b[j] else "!!"
                print(mark, "O:", a[j][:150])
                if a[j] != b[j]:
                    print(mark, "D:", b[j][:150])
            break
    else:
        print("identical for", n, "events; lengths", len(a), len(b))
