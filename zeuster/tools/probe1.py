import sys, os, pickle
sys.setrecursionlimit(200000)
from boot import *
ast = load_ast()
it = BootInterp()
Backend, Native, control = it.run_chunk(ast)
runtime = it.call(it.index(Backend, b"createRuntime"), [])[0]
state = it.call(it.index(Backend, b"bindPrimitives"), [runtime])[0]
C, count, F, metas = pickle.load(open('progdata.pkl', 'rb'))
tables, consts = build_data(it, C, F, metas)
# constant metatable emulation
cm = LuaTable(name="constmeta")
def cindex(a):
    ops, pc = a[0], a[1]
    idx = ops.h[0.0].h.get(key_norm(pc)) if 0.0 in ops.h else None
    return [consts.get(idx)]
from interp import key_norm
cm.h[b"__index"] = Builtin(cindex, "constindex")
for n in metas:
    tables[n].meta = cm
data = LuaTable({float(i): tables[i] for i in tables})
it.call(it.index(Backend, b"initialize"), [data])
factory = state.h[56.0]
protos = {}
for n, t in F.items():
    if set(t.keys()) == {1,2,3,4,5,6,7,8,9,10} and isinstance(t[3], Ref):
        protos[n] = t
res = {}
for n in sorted(protos):
    p = tables[n]
    caps = LuaTable(name="caps")
    try:
        r = it.call(factory, [p, caps])
    except LuaError as e:
        print(n, "ERR", e.value, getattr(e, 'line', None)); continue
    f = r[0]
    line = f.node.line if isinstance(f, LuaFunction) else repr(f)
    res[n] = (protos[n][1], line)
import collections
print(collections.Counter((repr(v[0]), v[1]) for v in res.values()).most_common(12))
print(len(set(v[1] for v in res.values())))
pickle.dump(res, open('proto_interp_lines.pkl','wb'))
