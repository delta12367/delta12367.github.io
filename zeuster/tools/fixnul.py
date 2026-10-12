import sys
for p in sys.argv[1:]:
    d = open(p, "rb").read()
    n = d.count(b"\x00")
    d = d.replace(b"\x00", b"\\0")
    open(p, "wb").write(d)
    print(p, "replaced", n, "remaining", open(p, "rb").read().count(b"\x00"))
