"""Rename locals of the decompiled file from an explicit map (names chosen by reading the code).

  python rename_map.py list  SRC OUT.json            every local with a generic name: binding id, name,
                                                     declaration line, scope lines, first uses
  python rename_map.py apply SRC MAP.json OUT        MAP = {binding id: new name}

A binding id is the declaration's position as luau-ast reports it ("line,col - line,col"). A new name
is only applied when no other local with that name has an overlapping scope and no global of that name
is used inside the local's scope (the same checks as rename.py); otherwise it gets a numeric suffix.
Renaming only changes names, so `luau-compile --text -g0 --dump-constants` of the result must equal
that of the input (see check_same_code).
"""
import collections, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rename

GENERIC = re.compile(r"^(v|tbl|fn|value|n|num|flag|str|obj|arg|var|k|x|y|res|result|list|clone|found|index|"
                     r"len|ok|err|inst|tmp|item|values|elem|entry)\d*$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def analyse(src_path):
    raw = subprocess.run([rename.LUAU_AST, src_path], capture_output=True, check=True).stdout
    ast = json.loads(raw.decode("utf-8", "replace"))
    A = rename.Analyzer(ast["root"])
    A.block(ast["root"])
    return A


def is_generic(name):
    return name != "_" and bool(GENERIC.match(name))


def cmd_list(src_path, out_path):
    A = analyse(src_path)
    rows = []
    for L in sorted(A.locals.values(), key=lambda L: L.decl):
        if not is_generic(L.name):
            continue
        rows.append({
            "id": L.key,
            "name": L.name,
            "kind": L.kind,
            "line": L.decl[0][0] + 1,
            "scope": [L.scope[0][0] + 1, L.scope[1][0] + 1],
            "uses": sorted(set(r[0][0] + 1 for r in L.refs))[:12],
        })
    json.dump(rows, open(out_path, "w"), indent=0)
    print("generic locals", len(rows), "of", len(A.locals))


def cmd_apply(src_path, map_path, out_path):
    want = json.load(open(map_path))
    A = analyse(src_path)
    by_name = collections.defaultdict(list)
    for L in A.locals.values():
        by_name[L.name].append(L)
    applied = suffixed = skipped = 0
    for L in sorted(A.locals.values(), key=lambda L: L.decl):
        base = want.get(L.key)
        if not base or base == L.name:
            continue
        if not IDENT.match(base) or base in rename.KEYWORDS:
            skipped += 1
            continue
        for k in range(1, 50):
            cand = base if k == 1 else "%s%d" % (base, k)
            bad = any(M is not L and rename.scopes_overlap(M.scope, L.scope) for M in by_name.get(cand, ()))
            if not bad:
                bad = any(L.scope[0] <= pos <= L.scope[1] for pos in A.globals.get(cand, ()))
            if not bad:
                break
        else:
            skipped += 1
            continue
        suffixed += cand != base
        by_name[L.name].remove(L)
        L.new = L.name = cand
        by_name[cand].append(L)
        applied += 1
    lines = open(src_path, "rb").read().split(b"\n")
    edits = collections.defaultdict(list)
    for L in A.locals.values():
        if L.new:
            for (a, b) in [L.decl] + L.refs:
                edits[a[0]].append((a[1], b[1], L.new.encode()))
    for ln, es in edits.items():
        s = lines[ln]
        for c1, c2, new in sorted(set(es), reverse=True):
            s = s[:c1] + new + s[c2:]
        lines[ln] = s
    open(out_path, "wb").write(b"\n".join(lines))
    print("renamed", applied, "(with a suffix", suffixed, ") skipped", skipped)


def compiled(path, luau_compile):
    return subprocess.run([luau_compile, "--text", "-g0", "--dump-constants", path], capture_output=True,
                          check=True).stdout


def check_same_code(a, b, luau_compile):
    """True when the two files compile to the same instructions and constants (names and lines aside)"""
    return compiled(a, luau_compile) == compiled(b, luau_compile)


if __name__ == "__main__":
    if sys.argv[1] == "list":
        cmd_list(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "apply":
        cmd_apply(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == "same":
        ok = check_same_code(sys.argv[2], sys.argv[3], sys.argv[4])
        print("same code" if ok else "CODE DIFFERS")
        sys.exit(0 if ok else 1)
