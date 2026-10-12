import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    from lifter2 import Lifter, Captures
    L = Lifter()
    pid = int(sys.argv[1])
    I = L.I
    f = I.call(I.real_factory, [L.tables[pid], Captures()])[0]
    node = f.node
    lines = []
    def walk(n):
        from luaparse import Node
        if isinstance(n, Node):
            if getattr(n, "line", None):
                lines.append(n.line)
            for c in n.a:
                walk(c)
        elif isinstance(n, (list, tuple)):
            for c in n:
                walk(c)
    walk(node)
    print("template", L.F[pid][1], "lines", min(lines), max(lines))
t = threading.Thread(target=main); t.start(); t.join()
