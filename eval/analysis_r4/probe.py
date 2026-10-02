import sys, time, datetime, signal, random
from pathlib import Path
sys.path.insert(0,'.')
from eval.run_det import CTX
from pgw.pd13 import build_protector
pB=build_protector(Path('eval/data3'),ner=None,l3=True,today=datetime.date(2026,10,1))
msg=lambda t:[{"role":"user","content":t}]
pB.analyze(msg("warm"),CTX)
class TO(Exception):pass
def h(*a): raise TO()
signal.signal(signal.SIGALRM,h)
rnd=random.Random(1)
def n(k): return ''.join(rnd.choice('abcdefghij') for _ in range(k))
P={
 'open_braces':lambda N:'{'*N,
 'open_brace_key':lambda N:'{"a":'*(N//5),
 'open_brackets':lambda N:'['*N,
 'brace_lines':lambda N:'{ "name": "x",\n'*(N//16),
 'nested_unbalanced':lambda N:'{"a":[{"b":'*(N//10),
 'open_angle':lambda N:'<a '*(N//3),
 'open_tags':lambda N:'<a><b>'*(N//6),
 'calls_open':lambda N:'f('*(N//2),
 'digit_spaces':lambda N:'1 2 3 4 '*(N//8),
 'digits_long':lambda N:'1'*N,
 'csv_commas':lambda N:','*N,
 'csv_rows':lambda N:('a,b,c,d,e\n')*(N//10),
 'quotes':lambda N:'"'*N,
 'dots':lambda N:'.'*N,
 'tabs_pipes':lambda N:'| a '*(N//4),
 'words':lambda N:' '.join(n(5) for _ in range(N//6)),
 'long_token':lambda N:n(N),
 'backslashes':lambda N:'\\'*N,
 'kv_lines':lambda N:'key: value\n'*(N//11),
 'eq_amp':lambda N:'a=b&'*(N//4),
}
for name,f in P.items():
    row=[]
    for N in (5000,20000):
        t=f(N); signal.alarm(120); t0=time.perf_counter()
        try: pB.analyze(msg(t),CTX); row.append(round(time.perf_counter()-t0,3))
        except TO: row.append('>120')
        except RecursionError: row.append('recerr')
        signal.alarm(0)
    print(name,row,flush=True)
