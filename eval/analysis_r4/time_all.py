import sys, json, time, datetime, gc
from pathlib import Path
sys.path.insert(0,'.')
from eval.run_det import CTX
arm=sys.argv[1]
if arm=='A':
    from eval.run_det import build_protector
    p=build_protector(Path('eval/data3'),True,ner='en_core_web_lg')
else:
    from pgw.pd13 import build_protector
    p=build_protector(Path('eval/data3'),ner='en_core_web_lg',l3=(arm=='B'),today=datetime.date(2026,10,1))
items=[json.loads(l) for l in open('/home/user/v3-unsealed/out/test3.jsonl')]
p.analyze([{"role":"user","content":"warm up John Smith"}],CTX)
out=open(sys.argv[2],'w')
for it in items:
    t=it['text'];t0=time.perf_counter()
    p.analyze([{"role":"user","content":t}],CTX)
    dt=time.perf_counter()-t0
    out.write(json.dumps({"id":it['id'],"family":it['family'],"len":len(t),"lines":t.count('\n')+1,"s":dt})+"\n");out.flush()
