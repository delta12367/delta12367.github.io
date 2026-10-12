import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)


def main():
    import lifter2
    from lifter2 import Lifter
    L = Lifter()
    pid = int(sys.argv[1])
    want = float(sys.argv[2])
    I = L.I
    orig = lifter2.SymInterp.exec_stmt
    shown = [0]

    def traced(self, s, sc):
        try:
            pc = dict.get(self.fscope.vars, "pc")
        except Exception:
            pc = None
        if pc == want and self.mode == "loop" and shown[0] < 400:
            shown[0] += 1
            print("line", s.line, s.k, flush=True)
        return orig(self, s, sc)
    lifter2.SymInterp.exec_stmt = traced
    res = L.lift(pid)
    for bid, b in res["blocks"].items():
        if b["pc"] == want:
            st = b["state"]
            print("state", {k: v for k, v in st.items() if not str(k).startswith("('mem'")})


t = threading.Thread(target=main)
t.start()
t.join()
