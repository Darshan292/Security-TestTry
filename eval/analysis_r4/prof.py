import sys, json, time, datetime, cProfile, pstats, tracemalloc, resource, collections
from pathlib import Path
sys.path.insert(0,'.')
from eval.run_det import CTX
from pgw.pd13 import build_protector
import pgw.pd13.protector as P
from pgw.detect.ner import NerDetector
items={}
for l in open('/home/user/v3-unsealed/out/test3.jsonl'):
    j=json.loads(l); items[j['id']]=j
tid=sys.argv[1]; text=items[tid]['text']
l3=sys.argv[2]=='B'
p=build_protector(Path('eval/data3'),ner='en_core_web_lg',l3=l3,today=datetime.date(2026,10,1))
msg=lambda t:[{"role":"user","content":t}]
p.analyze(msg("warm John Smith"),CTX)
# timers
T=collections.defaultdict(float); N=collections.defaultdict(int); SZ=collections.defaultdict(list)
def wrap(obj,name,label,sz=None):
    f=getattr(obj,name)
    def w(*a,**k):
        t0=time.perf_counter(); r=f(*a,**k); T[label]+=time.perf_counter()-t0; N[label]+=1
        if sz: SZ[label].append(sz(a,r))
        return r
    setattr(obj,name,w)
wrap(p.ner,'detect','ner.detect',lambda a,r:len(a[0]))
wrap(P,'extra_views','pd13.views(extra_views)',lambda a,r:[len(v.text) for v in r])
wrap(P,'parse','pd13.records.parse')
wrap(P,'l2_shapes','pd13.shapes',lambda a,r:len(a[0]))
wrap(P,'field_view','pd13.field_view')
wrap(P,'name_cells','pd13.name_cells')
wrap(P,'d0_views','d0_views(normalize.views)',lambda a,r:None)
wrap(P.recognizers,'detect_d2','recognizers.detect_d2',lambda a,r:len(a[0]))
wrap(P,'detect_person_cues','detect_person_cues',lambda a,r:len(a[0]))
wrap(p.roster,'detect','roster.detect',lambda a,r:len(a[0]))
wrap(P.Protector,'detect_text','Protector.detect_text(base D0 loop)')
wrap(P.l4,'apply','pd13.policy(l4.apply)')
if p.semantics: wrap(p.semantics,'analyse','pd13.semantics(L3)')
wrap(p,'_detect','TOTAL _detect')
t0=time.perf_counter(); p.analyze(msg(text),CTX); tot=time.perf_counter()-t0
print('analyze total',round(tot,3),'len',len(text))
for k,v in sorted(T.items(),key=lambda x:-x[1]): print(f'  {k:42s} {v:8.3f}s calls={N[k]}')
for k in ('ner.detect','pd13.views(extra_views)','pd13.shapes','recognizers.detect_d2'):
    s=SZ.get(k)
    if s: print(k,'sizes',s[:12] if k!='ner.detect' else (len(s),max(s),sum(s)))
from pgw.normalize import views as dv, normalize
vs=dv(text); print('D0 views',[(n,len(v)) for n,v,_ in vs])
print('base len',len(normalize(text)[0]))
