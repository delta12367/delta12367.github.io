"""asset.rbxm (dumped from the script's embedded asset bundle) -> asset_tree.luau (class, name, children)"""
import collections, rbxm, sys
ic, props, parent, order = rbxm.read(sys.argv[1] if len(sys.argv) > 1 else "asset.rbxm")
kids = collections.defaultdict(list)
for r in order:
    kids[parent[r]].append(r)


def lit(s):
    return '"' + "".join(chr(b) if 32 <= b < 127 and b not in (34, 92) else "\\%d" % b for b in s) + '"'


def node(r, d):
    nm = props.get(r, {}).get("Name", b"")
    ch = ", ".join(node(c, d + 1) for c in kids[r])
    return "{%s, %s, {%s}}" % (lit(ic[r].encode()), lit(nm), ch)


out = "local ASSET_TREE = {" + ",\n".join(node(r, 0) for r in kids[-1]) + "}\n"
open("asset_tree.luau", "w").write(out)
print("roots", len(kids[-1]), "instances", len(order))
