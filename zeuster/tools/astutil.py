from luaparse import Node


def is_step(s):
    return (isinstance(s, Node) and s.k == "CallStat" and s.a[0].k == "Call" and s.a[0].a[0].k == "Name"
            and s.a[0].a[0].a[0] == "__step")


def strip_steps(x):
    stack = [x]
    while stack:
        n = stack.pop()
        if isinstance(n, Node):
            for i, c in enumerate(n.a):
                if isinstance(c, list):
                    if any(is_step(y) for y in c):
                        c = [y for y in c if not is_step(y)]
                        n.a[i] = c
                    stack.append(c)
                elif isinstance(c, (Node, tuple)):
                    stack.append(c)
        elif isinstance(n, (list, tuple)):
            for c in n:
                if isinstance(c, (Node, list, tuple)):
                    stack.append(c)
    return x
