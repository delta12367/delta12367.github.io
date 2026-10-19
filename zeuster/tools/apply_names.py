"""Apply the names and section comments proposed for the decompiled file, and prove the result is the
same program.

  python apply_names.py SRC OUT out_00.json out_01.json ...

Each JSON file: {"renames": {binding id: name}, "comments": [{"line": N, "text": "..."}]}. Renames go
through rename_map.py (scope-checked); each comment is inserted on its own line above line N of SRC with
that line's indentation. Afterwards the compiled code (luau-compile --text -g0 --dump-constants) of OUT
must equal that of SRC, so neither a name nor a comment can have changed what the program does.
"""
import json, os, re, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rename_map

LUAU_COMPILE = os.environ.get("LUAU_COMPILE", os.path.join(HERE, "..", "bin", "luau-compile"))


def main():
    src, out = sys.argv[1], sys.argv[2]
    renames, comments = {}, []
    for p in sys.argv[3:]:
        d = json.load(open(p))
        renames.update(d.get("renames", {}))
        comments += d.get("comments", [])
    tmp_map = out + ".map.json"
    json.dump(renames, open(tmp_map, "w"))
    rename_map.cmd_apply(src, tmp_map, out)
    os.remove(tmp_map)
    lines = open(out, encoding="utf-8").read().split("\n")
    seen = set()
    placed = 0
    for c in sorted(comments, key=lambda c: -int(c["line"])):
        n = int(c["line"])
        text = re.sub(r"\s+", " ", str(c["text"])).strip().lstrip("-").strip()
        if not text or n < 1 or n > len(lines) or n in seen:
            continue
        target = lines[n - 1]
        if not target.strip():
            continue
        seen.add(n)
        indent = target[:len(target) - len(target.lstrip(" "))]
        lines.insert(n - 1, indent + "-- " + text)
        placed += 1
    open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print("comments placed", placed, "of", len(comments))
    ok = rename_map.check_same_code(src, out, LUAU_COMPILE)
    print("compiled code identical to the input" if ok else "COMPILED CODE DIFFERS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
