"""Syntax-check the generated Lua and report per-function active-local maxima."""
import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    from luaparse import parse, Node
    src = open(sys.argv[1], encoding="utf-8").read()
    try:
        ast = parse(src)
    except SyntaxError as e:
        print("SYNTAX ERROR:", e)
        return
    print("parsed OK:", len(src.splitlines()), "lines")
    worst = []

    def block_locals(stmts, active, fname):
        # returns max active locals in this function body
        mx = active
        cur = active
        for s in stmts:
            k = s.k
            if k == "Local":
                cur += len(s.a[0])
                mx = max(mx, cur)
                for e in s.a[1]:
                    scan_expr(e)
            elif k == "LocalFunction":
                cur += 1
                mx = max(mx, cur)
                func(s.a[1], s.a[0])
            elif k in ("If",):
                scan_expr(s.a[0])
                mx = max(mx, block_locals(s.a[1], cur, fname))
                if s.a[2]:
                    mx = max(mx, block_locals(s.a[2], cur, fname))
            elif k == "While":
                scan_expr(s.a[0])
                mx = max(mx, block_locals(s.a[1], cur, fname))
            elif k == "Repeat":
                mx = max(mx, block_locals(s.a[0], cur, fname))
            elif k == "NumFor":
                mx = max(mx, block_locals(s.a[4], cur + 1, fname))
            elif k == "GenFor":
                mx = max(mx, block_locals(s.a[2], cur + len(s.a[0]), fname))
            elif k == "Do":
                mx = max(mx, block_locals(s.a[0], cur, fname))
            else:
                for x in s.a:
                    if isinstance(x, Node):
                        scan_expr(x)
                    elif isinstance(x, list):
                        for y in x:
                            if isinstance(y, Node):
                                scan_expr(y)
        return mx

    def scan_expr(e):
        if not isinstance(e, Node):
            return
        if e.k == "Function":
            func(e, "?")
            return
        for x in e.a:
            if isinstance(x, Node):
                scan_expr(x)
            elif isinstance(x, list):
                for y in x:
                    if isinstance(y, Node):
                        scan_expr(y)
                    elif isinstance(y, tuple):
                        for z in y:
                            if isinstance(z, Node):
                                scan_expr(z)

    def func(fn, name):
        params, vararg, body = fn.a
        mx = block_locals(body, len(params), name)
        worst.append((mx, fn.line))
    mx = block_locals(ast, 0, "main")
    worst.append((mx, 0))
    worst.sort(reverse=True)
    print("max active locals (top 5):", worst[:5])


t = threading.Thread(target=main)
t.start()
t.join()
