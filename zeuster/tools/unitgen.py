"""Standalone units for the oracle's unit round.

For each requested function, write its decompiled text as a closure factory that takes the upvalues
as parameters (`function(__up0, __up1, ...) return function(...) ... end end`), and an upvalue spec
both oracle runs use to build identical upvalue values. The original run builds the same function
with the VM's own closure constructor and boxes holding those values, so the two can be called with
the same arguments and their traces compared, without the parent having to reach the code that
creates the function.

usage: python unitgen.py OUT_UNITS.lua OUT_SPEC.luau PID...   (ORACLE_REG=1 for registered children)
"""
import sys, os, pickle, re, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
HERE = os.path.dirname(os.path.abspath(__file__))


def upvalue_values(text, nup):
    """per upvalue: None (mock object) or a number that passes the integrity checks the function makes
    directly on it, `up + a ~= b` / `up + a == b`"""
    vals = [None] * nup
    for m in re.finditer(r"\b__up(\d+) \+ ([-0-9.e]+) [~=]= ([-0-9.e]+)\b", text):
        i = int(m.group(1))
        if i < nup and vals[i] is None:
            vals[i] = float(m.group(3)) - float(m.group(2))
    return vals


def capture_layouts(results):
    """pid -> per upvalue True when the VM passes it as a box (a local captured by reference:
    {[2] = register, [3] = registers}) and False when it passes the value itself; taken from the
    closure instruction that creates the function (a pass-through of the creator's own upvalue
    has the creator's layout)"""
    sites = {}
    for pid, blocks in results.items():
        for b in blocks.values():
            for p in b["paths"]:
                for d, st in p[2]:
                    stack = [st]
                    while stack:
                        x = stack.pop()
                        if isinstance(x, tuple):
                            if len(x) == 3 and x[0] == "closure" and isinstance(x[1], int):
                                sites.setdefault(x[1], (pid, x[2]))
                            stack.extend(x)
                        elif isinstance(x, list):
                            stack.extend(x)
    memo = {}

    def layout(pid, depth=0):
        if pid in memo:
            return memo[pid]
        site = sites.get(pid)
        out = []
        if site is not None and depth < 50:
            parent, caps = site
            for c in caps:
                if c[0] == "rref":
                    out.append(True)
                elif c[0] == "u":
                    up = layout(parent, depth + 1)
                    out.append(up[c[1]] if c[1] < len(up) else False)
                else:
                    out.append(False)
        memo[pid] = out
        return out
    return layout


def main():
    import natives, assemble, constfold
    from decompile import Decompiler
    out_units, out_spec = sys.argv[1], sys.argv[2]
    pids = [int(x) for x in sys.argv[3:]]
    head_lines = open(os.path.join(HERE, "head.lua"), encoding="utf-8").read().split("\n")
    nat = natives.load(head_lines)
    results = assemble.load_results([os.path.join(HERE, p) for p in ("root.pkl", "noroot.pkl")])
    constfold.ELIGIBLE = constfold.find_eligible(results)
    C, count, F, metas = pickle.load(open(os.path.join(HERE, "progdata.pkl"), "rb"))
    reserved = assemble.collect_globals(results)
    native_src = {}
    for name, ns in nat.items():
        native_src[name] = (lambda ns: (lambda upnames, indent, taken: natives.render(ns, upnames, indent)))(ns)
    D = Decompiler(results, lambda pid: int(F[pid][6]), native_src, assemble.hostfn_factory(head_lines), reserved)
    D.native_meta = nat
    units = ["local __select, __pack, __move = select, table.pack, table.move", "local __UNITS = {}"]
    spec = []
    layout = capture_layouts(results)
    for pid in pids:
        ref = F[pid][2]
        nup = len(F[ref.i]) if type(ref).__name__ == "Ref" else 0
        names = ["__up%d" % i for i in range(nup)]
        text = D.function_text(pid, names, "", 0, set(names))
        units.append("__UNITS[%d] = function(%s)\n    return %s\nend" % (pid, ", ".join(names), text.replace("\n", "\n    ")))
        vals = upvalue_values(text, nup)
        boxed = layout(pid)
        spec.append("[%d] = {%d, {%s}, {%s}}" % (pid, nup, ", ".join("[%d] = %r" % (i + 1, v) for i, v in enumerate(vals) if v is not None),
                                              ", ".join("[%d] = true" % (i + 1) for i, b in enumerate(boxed) if b and i < nup)))
    units.append("__H.mkunit = function(pid, vals, n)\n    return __UNITS[pid](table.unpack(vals, 1, n))\nend")
    with open(out_units, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(units) + "\n")
    with open(out_spec, "w", encoding="utf-8", newline="\n") as f:
        f.write("{" + ",\n".join(spec) + "}")
    print("units", len(pids))


if __name__ == "__main__":
    t = threading.Thread(target=main)
    t.start()
    t.join()
