"""Build the oracle runs: original (Luraph VM, instrumented for coverage) and decompiled, both inside the mock."""
import sys
pre = open("harness_pre.luau", encoding="utf-8").read()
pre = open("asset_tree.luau", encoding="utf-8").read() + pre
# the instrumentation hooks are upvalues: the script changes function and thread environments
# with setfenv, which must not hide them
HOOKS = "local __COV, __STEP, __ITER, __REG = __H.COV, __H.STEP, __H.ITER, __H.REG\n"
import os as _os
if _os.path.exists("fuzz_pids.txt"):
    # one line per fuzz round
    _rounds = [[int(x) for x in ln.split()] for ln in open("fuzz_pids.txt") if ln.strip()]
    pre = pre.replace("    local STEPMAX = ", "    local FUZZ_ROUNDS = {%s}\n    local STEPMAX = " %
                      ", ".join("{%s}" % ", ".join(map(str, r)) for r in _rounds), 1)


def lua_patch_table(path):
    """PATCH lines of a previous run -> Lua table literal"""
    def dec(tok):
        k, _, v = tok.partition(":")
        if tok == "nil":
            return "nil"
        if k == "n":
            return v if v not in ("inf", "-inf", "nan", "-nan") else "(0/0)"
        if k == "s":
            return '"' + "".join("\\%d" % b for b in bytes.fromhex(v)) + '"'
        if k == "b":
            return "true" if v == "1" else "false"
        raise ValueError(tok)
    rows = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        if ln.startswith("PATCH "):
            _, pid, slot, key, val = ln.split()
            if val.startswith("?:"):
                continue
            rows.append("{%s, %s, %s, %s}" % (pid, slot, dec(key), dec(val)))
    return "local __PATCHES = {" + ",\n".join(rows) + "}\n"


def build_orig(src_path, out_path, patches=None):
    src = open(src_path, encoding="utf-8").read()
    # the loader wrapper around the VM (not part of the script) reads the clock once at start;
    # the mock clock is shared state, so keep it out of the comparison
    assert src.count("    started = os.clock(),\n") == 1
    src = src.replace("    started = os.clock(),\n", "    started = 0,\n")
    old_step = "local function __step(id)\n"
    assert src.count(old_step) == 1
    src = src.replace(old_step, "local function __step(id)\n    __STEP()\n    do return end\n")
    import re as _re
    src = _re.sub(r"(?m)^(\s*)a = function\(\.\.\.\)$", lambda m: m.group(0) + "\n" + m.group(1) + "    local __act = {}", src)
    n_it = len(_re.findall(r"\bin \.\.\.\s*do\b", src))
    src = _re.sub(r"\bin \.\.\.\s*do\b", "in __ITER(...) do", src)
    print("orig generic-for sites", n_it)
    src = iter_rewrite(src)
    n = src.count("local opcode = opcodes[pc]")
    src = src.replace("local opcode = opcodes[pc]", "local opcode = opcodes[pc]; __COV(opcodes, pc, __act)")
    tail = "return runApplication()"
    assert src.rstrip().endswith(tail)
    src = src.rstrip()[: -len(tail)] + """
do
    local __runtime = Backend.createRuntime()
    local __state = Backend.bindPrimitives(__runtime)
    local __data = Backend.loadProgramData()
    Backend.initialize(__data)
    local __protos = {}
    for i, t in pairs(__data) do
        if type(t) == "table" and type(rawget(t, 1)) == "number" and type(rawget(t, 3)) == "table" then
            __protos[rawget(t, 3)] = i
        end
    end
    -- operand-table patches: snapshot now, print the differences after the run; with PATCHES
    -- set, apply a previous run's final patches first (steady state, no first-run resolvers)
    local __slots = {3, 4, 5, 7, 8, 9, 10}
    local __snap = {}
    for i, t in pairs(__data) do
        if type(t) == "table" and type(rawget(t, 1)) == "number" and type(rawget(t, 3)) == "table" then
            for _, s in ipairs(__slots) do
                local tt = rawget(t, s)
                if type(tt) == "table" then
                    local c = {}
                    local k = next(tt)
                    while k ~= nil do
                        c[k] = rawget(tt, k)
                        k = next(tt, k)
                    end
                    __snap[#__snap + 1] = {i, s, tt, c}
                end
            end
        end
    end
    local __P = __PATCHES_REF
    if __P then
        for _, p in ipairs(__P) do
            rawset(rawget(__data[p[1]], p[2]), p[3], p[4])
        end
    end
    do
        -- register every closure the VM creates (paired with __REG in the decompiled build)
        local __nc = __state[56]
        __state[56] = function(p, c, ...)
            local f = __nc(p, c, ...)
            local pid = type(p) == "table" and __protos[rawget(p, 3)]
            if pid then
                __H.REG(f, pid)
            end
            return f
        end
    end
    local __entry = __state[56](__data[1], __state[2])
    __H.run(__entry, __protos)
    local function __enc(v)
        local tv = type(v)
        if tv == "number" then return string.format("n:%.17g", v) end
        if tv == "string" then return "s:" .. (v:gsub(".", function(c) return string.format("%02x", string.byte(c)) end)) end
        if tv == "boolean" then return v and "b:1" or "b:0" end
        if tv == "nil" then return "nil" end
        return "?:" .. tv
    end
    for _, e in ipairs(__snap) do
        local tt, c = e[3], e[4]
        local seen = {}
        local k = next(tt)
        while k ~= nil do
            seen[k] = true
            local v = rawget(tt, k)
            if c[k] ~= v then
                __H.rawprint("PATCH " .. e[1] .. " " .. e[2] .. " " .. __enc(k) .. " " .. __enc(v))
            end
            k = next(tt, k)
        end
        for k2 in pairs(c) do
            if not seen[k2] then
                __H.rawprint("PATCH " .. e[1] .. " " .. e[2] .. " " .. __enc(k2) .. " nil")
            end
        end
    end
end
"""
    head = (lua_patch_table(patches) + "local __PATCHES_REF = __PATCHES\n") if patches else "local __PATCHES_REF = nil\n"
    open(out_path, "w", encoding="utf-8").write(pre + "\n" + HOOKS + head + src)
    print("orig instrumented dispatch sites", n)


def add_steps(src):
    import re
    out = []
    for ln in src.split("\n"):
        out.append(ln)
        m = re.match(r"^(\s*)(while .* do|for .* do|repeat)$", ln)
        if m:
            out.append(m.group(1) + "    __STEP();")
    return "\n".join(out)


def iter_rewrite(src):
    import re
    out = []
    n = 0
    pending = None     # indentation of a for-in header still waiting for its `do`
    for ln in src.split("\n"):
        if pending is not None:
            ind = len(ln) - len(ln.lstrip())
            if re.search(r"[\s})\]]do$", ln) and ind <= pending + 4 and ln.lstrip().startswith(("}", ")", "]")):
                ln = ln[:-2].rstrip() + ") do"
                pending = None
            out.append(ln)
            continue
        m = re.match(r"^(\s*)for (.+?) in (.+) do$", ln)
        if m and not m.group(3).startswith("__ITER("):
            ln = "%sfor %s in __ITER(%s) do" % (m.group(1), m.group(2), m.group(3))
            n += 1
        else:
            m = re.match(r"^(\s*)for ([\w, ]+?) in (.+)$", ln)
            if m and not re.search(r"\bdo$", ln) and m.group(3).rstrip().endswith(("{", "(")):
                ln = "%sfor %s in __ITER(%s" % (m.group(1), m.group(2), m.group(3))
                pending = len(m.group(1))
                n += 1
        out.append(ln)
    assert pending is None
    print("dec generic-for sites", n)
    return "\n".join(out)


def build_dec(src_path, out_path):
    src = add_steps(iter_rewrite(open(src_path, encoding="utf-8").read()))
    out = pre + "\n" + HOOKS + "local function __MAIN(...)\n" + src + "\nend\n__H.run(__MAIN)\n"
    open(out_path, "w", encoding="utf-8").write(out)


if __name__ == "__main__":
    if len(sys.argv) > 3:
        build_orig(sys.argv[1], "run_orig2.luau", sys.argv[3])
    build_orig(sys.argv[1], "run_orig.luau")
    build_dec(sys.argv[2], "run_dec.luau")
