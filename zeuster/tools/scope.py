"""Local declaration placement for webs in the structured AST."""
import collections
from ir import walk, stmt_exprs, stmt_defs, term_exprs
from opt import stmt_exprs_any


def stmt_refs(s):
    """webs referenced by an AST statement's own expressions (not nested blocks)"""
    k = s[0]
    out = []
    if k == "ir":
        st = s[1]
        for e in stmt_exprs_any(st):
            for x in walk(e):
                if x[0] == "w":
                    out.append(x[1])
        if st[0] == "set" and st[1][0] == "w":
            out.append(st[1][1])
        if st[0] == "mset":
            for t in st[1]:
                if t[0] == "w":
                    out.append(t[1])
    elif k in ("if", "while"):
        for x in walk(s[1]):
            if x[0] == "w":
                out.append(x[1])
    elif k == "return":
        for e in s[1]:
            for x in walk(e):
                if x[0] == "w":
                    out.append(x[1])
    elif k == "fornum":
        for e in (s[2], s[3], s[4]):
            if e is not None:
                for x in walk(e):
                    if x[0] == "w":
                        out.append(x[1])
    elif k == "forin":
        for e in s[2]:
            for x in walk(e):
                if x[0] == "w":
                    out.append(x[1])
    return out


def child_blocks(s):
    k = s[0]
    if k == "if":
        return [s[2], s[3]]
    if k == "while":
        return [s[2]]
    if k == "repeat":
        return [s[1]]
    if k in ("fornum", "forin"):
        return [s[5]]
    if k == "do":
        return [s[1]]
    return []


def place_locals(body, webinfo):
    """returns new body with ('local', webs, init_stmt_or_None) statements inserted"""
    chains = collections.defaultdict(list)
    loopvars = set()

    def visit(block, prefix):
        bid = id(block)
        for i, s in enumerate(block):
            chain = prefix + ((bid, i),)
            for w in stmt_refs(s):
                chains[w].append(chain)
            if s[0] == "repeat":
                # until-condition sees body locals
                inner = s[1]
                cchain = chain + ((id(inner), len(inner)),)
                for x in walk(s[2]):
                    if x[0] == "w":
                        chains[x[1]].append(cchain)
            if s[0] in ("fornum", "forin"):
                vs = [s[1]] if s[0] == "fornum" else s[1]
                for v in vs:
                    loopvars.add(v)
            for cb in child_blocks(s):
                visit(cb, chain)
    visit(body, ())
    decl = collections.defaultdict(lambda: collections.defaultdict(list))  # blockid -> idx -> [webs]
    for w, chs in chains.items():
        if w in loopvars:
            continue
        info = webinfo.get(w, {})
        if info.get("param") is not None:
            continue
        if info.get("entry_used"):
            # value flows from function entry (nil): declare at top level
            first = min(c[0][1] for c in chs)
            decl[id(body)][first].append(w)
            continue
        depth = 0
        c0 = chs[0]
        while True:
            same_stmt = all(c[depth][1] == c0[depth][1] for c in chs)
            deeper = all(len(c) > depth + 1 for c in chs)
            if same_stmt and deeper and all(c[depth + 1][0] == c0[depth + 1][0] for c in chs):
                depth += 1
                continue
            break
        blk = c0[depth][0]
        first = min(c[depth][1] for c in chs)
        decl[blk][first].append(w)

    def rebuild(block):
        bid = id(block)
        out = []
        dmap = decl.get(bid, {})
        for i, s in enumerate(block):
            ws = dmap.get(i, [])
            s = rebuild_children(s)
            if ws:
                wset = set(ws)
                if s[0] == "ir" and s[1][0] == "set" and s[1][1][0] == "w" and s[1][1][1] in wset:
                    w = s[1][1][1]
                    selfref = any(x == ("w", w) for x in walk(s[1][2]))
                    if not selfref:
                        rest = [x for x in ws if x != w]
                        if rest:
                            out.append(("local", rest, None))
                        out.append(("local", [w], s[1][2]))
                        continue
                if s[0] == "ir" and s[1][0] == "mset" and all(t[0] == "w" for t in s[1][1]) and \
                        all(t[1] in wset for t in s[1][1]):
                    tw = [t[1] for t in s[1][1]]
                    selfref = any(x[0] == "w" and x[1] in tw for x in walk(s[1][2]))
                    if not selfref:
                        rest = [x for x in ws if x not in tw]
                        if rest:
                            out.append(("local", rest, None))
                        out.append(("local", tw, s[1][2]))
                        continue
                out.append(("local", ws, None))
            out.append(s)
        return out

    def rebuild_children(s):
        k = s[0]
        if k == "if":
            return ("if", s[1], rebuild(s[2]), rebuild(s[3]))
        if k == "while":
            return ("while", s[1], rebuild(s[2]))
        if k == "repeat":
            return ("repeat", rebuild(s[1]), s[2])
        if k == "fornum":
            return (k, s[1], s[2], s[3], s[4], rebuild(s[5]))
        if k == "forin":
            return (k, s[1], s[2], s[3], s[4], rebuild(s[5]))
        if k == "do":
            return ("do", rebuild(s[1]))
        return s
    return rebuild(body)
