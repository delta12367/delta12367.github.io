"""Build the oracle runs: original (Luraph VM, instrumented for coverage) and decompiled, both inside the mock."""
import sys
pre = open("harness_pre.luau", encoding="utf-8").read()


def build_orig(src_path, out_path):
    src = open(src_path, encoding="utf-8").read()
    old_step = "local function __step(id)\n"
    assert src.count(old_step) == 1
    src = src.replace(old_step, "local function __step(id)\n    __STEP()\n    do return end\n")
    import re as _re
    src = _re.sub(r"(?m)^(\s*)a = function\(\.\.\.\)$", lambda m: m.group(0) + "\n" + m.group(1) + "    local __act = {}", src)
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
    local __entry = __state[56](__data[1], __state[2])
    __H.run(__entry, __protos)
end
"""
    open(out_path, "w", encoding="utf-8").write(pre + "\n" + src)
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


def build_dec(src_path, out_path):
    src = add_steps(open(src_path, encoding="utf-8").read())
    out = pre + "\nlocal function __MAIN(...)\n" + src + "\nend\n__H.run(__MAIN)\n"
    open(out_path, "w", encoding="utf-8").write(out)


if __name__ == "__main__":
    build_orig(sys.argv[1], "run_orig.luau")
    build_dec(sys.argv[2], "run_dec.luau")
