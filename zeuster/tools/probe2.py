import sys, pickle, collections
sys.setrecursionlimit(200000)
from boot import *
from interp import key_norm
ast = load_ast()
# find all Function nodes assigned to 'a' with vararg and check body shapes
shapes = collections.Counter()
examples = {}
def walk(n):
    if isinstance(n, Node):
        if n.k == 'Assign' and len(n.a[0]) == 1 and n.a[0][0].k == 'Name' and n.a[0][0].a[0] == 'a' and n.a[1][0].k == 'Function':
            f = n.a[1][0]
            body = f.a[2]
            sh = tuple(s.k for s in body)
            # loop kind and first stmt in loop
            loops = [s for s in body if s.k in ('Repeat', 'While')]
            info = []
            for l in loops:
                lb = l.a[0] if l.k == 'Repeat' else l.a[1]
                info.append((l.k, lb[0].k, lb[-1].k, len(lb)))
            key = (sh, tuple(info))
            shapes[key] += 1
            examples.setdefault(key, f.line)
        for c in n.a:
            walk(c)
    elif isinstance(n, (list, tuple)):
        for c in n:
            walk(c)
walk(ast)
for k, v in shapes.most_common():
    print(v, examples[k], k)
