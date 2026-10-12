"""Fold calls of Luraph's encrypted-constant closures into the plaintext constant.

Luraph replaces some string constants with an inline closure called with an encrypted argument:
    (function(key) ...xor tables, a trap object, a register-file integrity scan... end)("\180Y...")
The closure is pure (no upvalues, only standard library calls). Its register-file scan
(`for k, v in next, registers`) only works inside the VM, so instead of lifting it we run the
original closure with the real Luraph VM in Luau and replace the call with the returned value.
Results are cached in constfold_cache.json.
"""
import json, os, subprocess, re

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "constfold_cache.json")
LUAU = os.environ.get("LUAU_BIN", os.path.join(HERE, "..", "bin", "luau"))          # luau CLI
SRC = os.environ.get("ZEUSTER_SRC", os.path.join(HERE, "..", "data", "z.lua"))     # obfuscated script
_cache = None
ELIGIBLE = set()
stats = {"folded": 0, "evaluated": 0, "failed": 0}


def find_eligible(results):
    """protos whose lifted IR scans the VM register file (`for k, v in next, registers`)"""
    out = set()
    for pid, blocks in results.items():
        if not blocks:
            continue
        hit = False
        for b in blocks.values():
            for p in b["paths"]:
                for d, st in p[2]:
                    if st[0] == "fginit" and st[3] == ("regfile",):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            out.add(pid)
    return out


def _load():
    global _cache
    if _cache is None:
        _cache = {}
        if os.path.exists(CACHE_PATH):
            _cache = json.load(open(CACHE_PATH))
    return _cache


def _key(pid, args):
    return "%d|%s" % (pid, "|".join(_enc(a) for a in args))


def _enc(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "b:" + ("1" if v else "0")
    if isinstance(v, float):
        return "n:" + repr(v)
    if isinstance(v, bytes):
        return "s:" + v.hex()
    raise TypeError(v)


def _lua_lit(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 2 ** 53:
            return "%d" % int(v)
        return repr(v)
    if isinstance(v, bytes):
        return '"' + "".join("\\%d" % c for c in v) + '"'
    raise TypeError(v)


def _run(reqs):
    src = open(SRC, encoding="utf-8").read()
    old_step = "local function __step(id)\n"
    src = src.replace(old_step, old_step + "    do return end\n", 1)
    tail = "return runApplication()"
    assert src.rstrip().endswith(tail)
    lines = ["local __genv = {}\nlocal getgenv = function() return __genv end\n" + src.rstrip()[: -len(tail)]]
    lines.append("""
local __runtime = Backend.createRuntime()
local __state = Backend.bindPrimitives(__runtime)
local __data = Backend.loadProgramData()
Backend.initialize(__data)
local function __emit(i, ok, ...)
    local n = select("#", ...)
    local parts = {}
    for j = 1, n do
        local v = select(j, ...)
        local t = type(v)
        if t == "string" then
            parts[j] = "s:" .. (v:gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
        elseif t == "number" then
            parts[j] = "n:" .. string.format("%.17g", v)
        elseif t == "boolean" then
            parts[j] = "b:" .. (v and "1" or "0")
        elseif t == "nil" then
            parts[j] = "nil"
        else
            parts[j] = "?:" .. t
        end
    end
    print("FOLD " .. i .. " " .. (ok and "ok" or "err") .. " " .. table.concat(parts, " "))
end
""")
    for i, (pid, args) in enumerate(reqs):
        lines.append("do local f = __state[56](__data[%d], {}); __emit(%d, pcall(f, %s)) end"
                     % (pid, i, ", ".join(_lua_lit(a) for a in args)))
    path = os.path.join(HERE, "_constfold_eval.luau")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    r = subprocess.run([LUAU, path], capture_output=True, text=True, timeout=600)
    out = {}
    for ln in r.stdout.split("\n"):
        m = re.match(r"^FOLD (\d+) (ok|err) ?(.*)$", ln)
        if m:
            out[int(m.group(1))] = (m.group(2) == "ok", m.group(3).split(" ") if m.group(3) else [])
    return out, r.stdout[-2000:] + r.stderr[-2000:]


def _dec(tok):
    if tok == "nil":
        return None
    k, v = tok.split(":", 1)
    if k == "s":
        return bytes.fromhex(v)
    if k == "n":
        return float(v)
    if k == "b":
        return v == "1"
    raise ValueError(tok)


def evaluate_many(reqs):
    cache = _load()
    todo = [(p, tuple(a)) for p, a in reqs if _key(p, a) not in cache]
    todo = list(dict.fromkeys(todo))
    if todo:
        res, log = _run(todo)
        for i, (p, a) in enumerate(todo):
            r = res.get(i)
            if r is None or not r[0] or any(t.startswith("?:") for t in r[1]):
                cache[_key(p, a)] = None
                stats["failed"] += 1
            else:
                cache[_key(p, a)] = r[1]
                stats["evaluated"] += 1
        json.dump(cache, open(CACHE_PATH, "w"), indent=0)
    out = []
    for p, a in reqs:
        r = cache.get(_key(p, a))
        out.append(None if r is None else [_dec(t) for t in r])
    return out


def evaluate(pid, args):
    return evaluate_many([(pid, args)])[0]


def _const_args(args):
    vals = []
    for a in args:
        if a[0] != "k" or not (a[1] is None or isinstance(a[1], (bool, float, bytes))):
            return None
        vals.append(a[1])
    return tuple(vals)


def _candidate(e):
    if e[0] in ("callx", "callmx") and e[1][0] == "closure" and e[1][1] in ELIGIBLE and not e[1][2]:
        args = _const_args(e[2])
        if args is not None:
            return (e[1][1], args)
    return None


def fold_func(F, log=None):
    """replace calls of eligible closures with constant arguments by their results"""
    from ir import walk, subst, term_exprs, term_with_exprs
    from opt import stmt_exprs_any, map_stmt
    if not ELIGIBLE:
        return 0
    reqs = []
    for b in F.bbs.values():
        exprs = []
        for s in b.stmts:
            exprs.extend(stmt_exprs_any(s))
        exprs.extend(term_exprs(b.term))
        for e in exprs:
            for x in walk(e):
                c = _candidate(x)
                if c is not None:
                    reqs.append(c)
    if not reqs:
        return 0
    reqs = list(dict.fromkeys(reqs))
    res = dict(zip(reqs, evaluate_many(reqs)))
    n = [0]

    def fn(e):
        c = _candidate(e)
        if c is None:
            return None
        r = res.get(c)
        if r is None:
            return None
        if e[0] == "callmx" and len(r) != 1:
            return None
        n[0] += 1
        return ("k", r[0] if r else None)
    for b in F.bbs.values():
        out = []
        for s in b.stmts:
            if s[0] == "callstat" and _candidate(s[1]) is not None and res.get(_candidate(s[1])) is not None:
                n[0] += 1      # pure call whose result is unused
                continue
            out.append(map_stmt(s, lambda e: subst(e, fn)))
        b.stmts = out
        b.term = term_with_exprs(b.term, [subst(e, fn) for e in term_exprs(b.term)])
    stats["folded"] += n[0]
    if n[0] and log is not None:
        log.append("folded %d encrypted constants" % n[0])
    return n[0]
