import sys, threading
threading.stack_size(256 * 1024 * 1024 - 4096)
sys.setrecursionlimit(1000000)
def main():
    import lifter2
    from lifter2 import Lifter
    L = Lifter()
    ops = L.tables[L.F[404][3].i]
    res = L.lift(404)
    print("static writes to opcodes[146]:", [(n, k, v) for n, k, v in L.I.static_writes if k == 146.0][:5])
    for bid, b in res["blocks"].items():
        if b["pc"] in (264.0, 146.0, 233.0, 263.0, 368.0):
            st = b["state"] or {}
            mems = {k: v for k, v in st.items() if isinstance(k, tuple) and k[0] == "mem" and k[1] == id(ops)}
            print("block", bid, "pc", b["pc"], "opcode overlays:", {k[2]: v for k, v in mems.items()})
t = threading.Thread(target=main); t.start(); t.join()
