"""Compare the two traces call by call: the part before the first fuzz/unit call must be identical, and
each `fuzz`/`fuzzall`/`unit` call (one phase each) is compared on its own, so one difference does not
hide the rest.

A difference is accepted only in this form: the decompiled side has extra events directly before an
error both sides raise in the same way. That is `obj:m(args)` where looking m up fails (a number or nil
receiver): Luraph's VM looks the method up first (SELF) and fails before the arguments, Luau evaluates
the arguments first. Everything else is listed as unexplained.

usage: python3 unitdiff.py o.tr d.tr
"""
import sys, re, difflib, collections
from norm import norm

HEAD = re.compile(r"^(unit|fuzzall|fuzz) \S+$")
ERR = re.compile(r"^thread error ")


def segments(lines):
    segs = [("<main>", [])]
    for ln in lines:
        if HEAD.match(ln):
            segs.append((ln, []))
        else:
            segs[-1][1].append(ln)
    return segs


IDS = re.compile(r"([A-Za-z_:.()\[\]\-\^]*)#(\d+)")


def canon(seg):
    """number object ids (`<expr-#71009>`, `new:Part#12`, ...) by first appearance in the segment: an extra
    object made in an earlier call shifts every later id by one"""
    ids = {}

    def sub(m):
        key = (m.group(1), m.group(2))
        if key not in ids:
            ids[key] = len([k for k in ids if k[0] == m.group(1)]) + 1
        return "%s#@%d" % (m.group(1), ids[key])
    return [IDS.sub(sub, ln) for ln in seg]


def classify(a, b):
    """None if identical, 'args-before-failed-lookup' if every difference is extra decompiled-side
    events right before an error both sides report, else 'unexplained'"""
    if a == b:
        return None
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag != "insert":
            return "unexplained"
        # inserted on the decompiled side: must be followed by the same error on both sides
        if i1 >= len(a) or not ERR.match(a[i1]) or j2 >= len(b) or b[j2] != a[i1]:
            return "unexplained"
        if any(ERR.match(x) for x in b[j1:j2]):
            return "unexplained"
    return "args-before-failed-lookup"


def main():
    a = norm(open(sys.argv[1], encoding="utf-8", errors="replace").read().split("\n"))
    b = norm(open(sys.argv[2], encoding="utf-8", errors="replace").read().split("\n"))
    # the closing line carries the raw event count, reads included (see readcheck.py)
    print("totals:", [x for x in a if x.startswith("TRACE_END")], [x for x in b if x.startswith("TRACE_END")])
    a = [x for x in a if not x.startswith("TRACE_END")]
    b = [x for x in b if not x.startswith("TRACE_END")]
    sa, sb = segments(a), segments(b)
    print("segments", len(sa), len(sb))
    if [h for h, _ in sa] != [h for h, _ in sb]:
        print("segment headers differ")
    stats = collections.Counter()
    bad = []
    kinds = collections.Counter()
    for (ha, la), (hb, lb) in zip(sa, sb):
        if ha != "<main>":
            la, lb = canon(la), canon(lb)
        c = classify(la, lb) if ha == hb else "unexplained"
        stats[c or "identical"] += 1
        kinds[(ha.split()[0], c or "identical")] += 1
        if c == "unexplained":
            bad.append((ha, la, lb))
    print(dict(stats))
    for k, v in sorted(kinds.items()):
        print("  ", k, v)
    for ha, la, lb in bad[:int(sys.argv[3]) if len(sys.argv) > 3 else 15]:
        print("== unexplained:", ha, len(la), len(lb))
        sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
        shown = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            for x in la[max(0, i1 - 2):i2][:8]:
                print("   O:", x[:160])
            for x in lb[j1:j2][:8]:
                print("   D:", x[:160])
            print("   --")
            shown += 1
            if shown >= 3:
                break
    print("unexplained segments:", len(bad))


if __name__ == "__main__":
    main()
