"""Assemble the final readable Lua file from lifted prototypes."""
import sys, os, pickle, re, threading, time, collections
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
HERE = os.path.dirname(os.path.abspath(__file__))


def load_results(paths):
    res = {}
    for p in paths:
        d = pickle.load(open(p, "rb"))
        for k, v in d["results"].items():
            if v is not None:
                res[k] = v
    return res


def hostfn_factory(head_lines):
    cache = {}

    def get(e):
        name, line = e[1], e[2]
        if line in cache:
            return cache[line]
        i = line - 1
        ln = head_lines[i]
        m = re.match(r"^(\s*)local function (\w+)\((.*)\)\s*$", ln)
        if not m:
            cache[line] = "nil --[[hostfn %s]]" % name
            return cache[line]
        ind = m.group(1)
        body = []
        j = i + 1
        while j < len(head_lines) and head_lines[j] != ind + "end":
            body.append(head_lines[j].strip())
            j += 1
        txt = "function(%s) %s end" % (m.group(3), " ".join(b for b in body if b))
        cache[line] = txt
        return txt
    return get


def collect_globals(results):
    names = set()
    for blocks in results.values():
        for b in blocks.values():
            for p in b["paths"]:
                for d, st in p[2]:
                    for m in re.finditer(r"\('g', \('k', b'([A-Za-z_][A-Za-z0-9_]*)'\)\)", repr(st)):
                        names.add(m.group(1))
    return names


def main():
    import natives
    from decompile import Decompiler
    head_lines = open(os.path.join(HERE, "head.lua"), encoding="utf-8").read().split("\n")
    nat = natives.load(head_lines)
    print("natives", len(nat))
    srcs = sys.argv[2:] if len(sys.argv) > 2 else ["root.pkl", "noroot.pkl"]
    results = load_results([os.path.join(HERE, p) for p in srcs])
    print("protos", len(results))
    import constfold
    constfold.ELIGIBLE = constfold.find_eligible(results)
    print("encrypted-constant closures", sorted(constfold.ELIGIBLE))
    C, count, F, metas = pickle.load(open(os.path.join(HERE, "progdata.pkl"), "rb"))
    globs = collect_globals(results)
    native_idents = set()
    for ns in nat.values():
        for ln in ns.body:
            for m in re.finditer(r"\b(?:local\s+(?:function\s+)?)([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)", ln):
                for x in m.group(1).split(","):
                    native_idents.add(x.strip())
            for m in re.finditer(r"\bfunction\s*\(([^)]*)\)", ln):
                for x in m.group(1).split(","):
                    native_idents.add(x.strip())
            for m in re.finditer(r"\bfor\s+([A-Za-z_][A-Za-z0-9_\s,]*)\s+(?:=|in)", ln):
                for x in m.group(1).split(","):
                    native_idents.add(x.strip())
    reserved = globs | native_idents
    native_src = {}
    for name, ns in nat.items():
        native_src[name] = (lambda ns: (lambda upnames, indent, taken: natives.render(ns, upnames, indent)))(ns)

    def nregs_of(pid):
        return int(F[pid][6])
    D = Decompiler(results, nregs_of, native_src, hostfn_factory(head_lines), reserved)
    D.native_meta = nat
    t0 = time.time()
    text = D.function_text(1, [], "", 0, set())
    print("decompiled in", time.time() - t0)
    # the root function body becomes the main chunk
    lines = text.split("\n")
    assert lines[0].startswith("function(")
    body = lines[1:-1]
    body = [ln[4:] if ln.startswith("    ") else ln for ln in body]
    out = []
    out.append("--[[")
    out.append("    Zeuster Enhancements - deobfuscated")
    out.append("    Recovered from a Luraph-protected build by lifting the VM bytecode back to Lua.")
    out.append("    Variable names are reconstructed (heuristic); the original names were not present in the file.")
    out.append("]]")
    out.append("")
    # helpers the decompiled code uses on its own (vararg slices, multi-value table fills); bound here so
    # they keep working inside functions whose environment the script replaces with setfenv
    out.append("local __select, __pack, __move = select, table.pack, table.move")
    out.append("")
    out.extend(body)
    out_path = sys.argv[1]
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("wrote", out_path, len(out), "lines")
    import rename
    if os.path.exists(rename.LUAU_AST):
        rename.run(out_path, out_path)     # use-based names for the remaining generic locals
    else:
        print("luau-ast not found, names left as generated:", rename.LUAU_AST)
    logs = collections.Counter()
    for pid, info in D.cache.items():
        if info is None:
            logs["analyse failed"] += 1
            continue
        for l in info.log:
            logs[l.split(":")[0]] += 1
    print(logs.most_common(20))
    for pid, l in list(D.logs.items())[:20]:
        print(pid, l)


if __name__ == "__main__":
    t = threading.Thread(target=main)
    t.start()
    t.join()
