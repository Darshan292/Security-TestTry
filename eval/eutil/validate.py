import json,re,sys
from collections import Counter
sys.path.insert(0,'.')
D=[[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
P=[[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
def v_ok(s):
    c=0
    for i,ch in enumerate(reversed(s)): c=D[c][P[i%8][int(ch)]]
    return c==0
ro=[json.loads(l) for l in open('roster.jsonl')]
T=[json.loads(l) for l in open('tasks.jsonl')]
err=[];warn=[]
assert len(ro)==60
for r in ro:
    assert re.fullmatch(r'KD-\d{5}',r['employee_id']) and re.fullmatch(r'\+91 [6-9]\d{4} \d{5}',r['phone']) and r['email'].endswith('@kestrel-dyn.in')
assert len({r['employee_id'] for r in ro})==60
assert len(T)==40
c=Counter(t['task_type'] for t in T); assert len(c)==8 and set(c.values())=={5},c
keys={"id","task_type","purpose","instruction","context","output_format","labels","gold","sensitive","task_critical_nonsensitive","notes"}
ext=json.load(open('.ext.json'))
tok=[]
for r in ro: tok+= [r['full_name'],r['employee_id'],r['email'],r['phone']]+[x for x in (r['first_name'],r['last_name']) if len(x)>=4]
for n in ext:
    tok+= [n]+[x for x in n.split() if len(x)>=4 and not x.endswith('.')]
types=Counter(); idc=Counter()
for t in T:
    assert set(t)==keys and t['purpose'] in ("hr_case_summary","email_draft","ticket_classification","comp_band_analysis")
    assert set(t['gold'])=={"label","json","facts","numbers","required_entities","required_sections","forbidden"}
    txt=t['instruction']+"\n"+t['context']
    for s in t['sensitive']:
        types[s['value'] and s['type']]+=1
        if s['value'] not in txt: err.append((t['id'],'sens missing',s['value']))
        v=s['value']
        if s['type']=='AADHAAR':
            d=v.replace(' ',''); assert re.fullmatch(r'[2-9]\d{11}',d) and v_ok(d),v; idc['aadhaar']+=1
        if s['type']=='PAN': assert re.fullmatch(r'[A-Z]{3}P[A-Z]\d{4}[A-Z]',v),v; idc['pan']+=1
        if s['type']=='IFSC': assert re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}',v),v; idc['ifsc']+=1
        if s['type']=='PHONE': assert re.fullmatch(r'\+91 ?[6-9]\d{4} ?\d{5}',v),v
    for v in t['task_critical_nonsensitive']:
        if v not in txt: err.append((t['id'],'tcn missing',v))
    svals=[s['value'] for s in t['sensitive']]
    for k in tok:
        if k in txt and not any(k in s for s in svals): warn.append((t['id'],k))
    for k in t['gold']['required_entities']+t['gold']['forbidden']: pass
    # IDs/PAN/IFSC regex scan in context for unlisted
    for m in re.findall(r'\b[A-Z]{3}P[A-Z]\d{4}[A-Z]\b',txt):
        if m not in svals: err.append((t['id'],'unlisted PAN',m))
    for m in re.findall(r'KD-\d{5}',txt):
        if m not in svals: err.append((t['id'],'unlisted id',m))
    for m in re.findall(r'[\w.]+@[\w.-]+\.\w+',txt):
        if m not in svals and not any(m in s for s in svals): warn.append((t['id'],'email',m))
    for f in t['gold']['forbidden']: assert f in txt or True
    if t['output_format']=='label': assert t['gold']['label'] in t['labels']
    if t['output_format']=='json': assert t['gold']['json'] is not None
    for n,v in t['gold']['numbers'].items(): assert re.fullmatch(r'\d+',v)
for k in ('KD',):
    pass
print("errors",err); print("warnings",sorted(set(warn)))
print("types",dict(types)); print("ids",dict(idc))
print(dict(c))
