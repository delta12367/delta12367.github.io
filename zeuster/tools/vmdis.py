import pickle, sys
from luadata import Ref
C,count,F,metas=pickle.load(open('progdata.pkl','rb'))
pid=int(sys.argv[1]); a=int(sys.argv[2]); b=int(sys.argv[3])
r=F[pid]
def K(k,pc):
    t=F[r[k].i]
    if pc in t: return t[pc]
    return C.get(F[t[0].i].get(pc)) if 0 in t else None
for pc in range(a,b):
    print(pc, [F[r[k].i].get(pc) for k in (3,4,5)], F[r[10].i].get(pc), [K(k,pc) for k in (7,8,9)])
