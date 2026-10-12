"""Boot the obfuscated runtime concretely to obtain the VM state table."""
import sys, os, pickle, time
sys.setrecursionlimit(200000)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from luaparse import parse, Node
from interp import Interp, LuaTable, Builtin, LuaFunction, LuaError, Scope
from luadata import Ref


def load_ast():
    src = open(os.path.join(HERE, "head.lua"), encoding="utf-8").read()
    src += "\nreturn Backend, Native, control\n"
    from astutil import strip_steps
    return strip_steps(Node("Root", parse(src))).a[0]


class BootInterp(Interp):
    def __init__(self):
        super().__init__()
        G = self.G
        genv = LuaTable(name="genv")
        self.reg(G, "getgenv", lambda a: [genv])
        # loadstring mock: returns a factory that wraps f -> f
        def loadstring(a):
            return [Builtin(lambda b: [b[0] if b else None], "luraph_wrap")]
        self.reg(G, "loadstring", loadstring)
        self.reg(G, "setfenv", lambda a: [a[0]])
        self.reg(G, "getfenv", lambda a: [G])
        G.h[b"_G"] = G


def build_data(it, constants, fills, metas):
    """Convert parsed program data into LuaTables. Constant views keep their [0] index table and get a
    metatable that resolves lazily (like the original)."""
    tables = {n: LuaTable(name=f"data[{n}]") for n in fills}

    def conv(v):
        if isinstance(v, Ref):
            return tables[v.i]
        if isinstance(v, int) and not isinstance(v, bool):
            return float(v)
        return v
    for n, t in fills.items():
        h = tables[n].h
        for k, v in t.items():
            kk = float(k) if isinstance(k, int) and not isinstance(k, bool) else k
            h[kk] = conv(v)
    consts = {float(k): conv(v) for k, v in constants.items()}
    return tables, consts


if __name__ == "__main__":
    t0 = time.time()
    ast = load_ast()
    print("parsed", time.time() - t0)
    it = BootInterp()
    Backend, Native, control = it.run_chunk(ast)
    print("chunk ok", time.time() - t0)
    runtime = it.call(it.index(Backend, b"createRuntime"), [])[0]
    print("runtime", runtime, len(runtime.h))
    try:
        state = it.call(it.index(Backend, b"bindPrimitives"), [runtime])[0]
    except LuaError as e:
        print("LUA ERROR", e.value, "line", getattr(e, "line", None))
        raise SystemExit
    print("state", state, len(state.h), time.time() - t0)
    for k in sorted(state.h, key=lambda x: (str(type(x)), x)):
        print(k, state.h[k])
