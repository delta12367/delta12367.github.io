"""Over-approximate static reachability: can any lifted pc reach unlifted code?"""
import pickle, sys, collections
from luadata import Ref
C, count, F, metas = pickle.load(open('progdata.pkl', 'rb'))
pid = int(sys.argv[1])
src = sys.argv[2]
r = pickle.load(open(src, 'rb'))['results'][pid]
lifted = set(int(b['pc']) for b in r.values() if b['pc'] is not None)
P = F[pid]
ops = F[P[3].i]; A = F[P[4].i]; B = F[P[5].i]; Cs = F[P[10].i]
N = max(k for k in ops if isinstance(k, int))
written = collections.defaultdict(set)
if len(sys.argv) > 3:
    for name, kk, v in pickle.load(open(sys.argv[3], 'rb')):
        if isinstance(v, float):
            written[int(kk)].add(int(v))

def succs(pc):
    out = set()
    op = ops.get(pc)
    if op != 7:
        out.add(pc + 1)
    for t in (A, B, Cs):
        v = t.get(pc)
        if isinstance(v, (int, float)) and 0 <= v <= N:
            out.add(int(v) + 1)
    for v in written.get(pc, ()):
        if 0 <= v <= N:
            out.add(v + 1)
    return out
unl = set(range(1, N + 1)) - lifted
seen = set()
stack = list(lifted)
entry_into = collections.Counter()
while stack:
    x = stack.pop()
    for s in succs(x):
        if s in unl and s not in seen:
            seen.add(s)
            stack.append(s)
            if x in lifted:
                entry_into[s] += 1
print("unlifted", len(unl), "statically reachable from lifted (over-approx)", len(seen))
print("unreachable even over-approx:", len(unl - seen))
