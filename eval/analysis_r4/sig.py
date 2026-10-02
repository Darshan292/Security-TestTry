import collections, json, re, sys, unicodedata, datetime
sys.path.insert(0, "/home/user/security-testtry")
from pathlib import Path
from eval.run_det import CTX, load
from eval.run_v3 import TAXONOMY, _fold, complete_exposed
from pgw.pd13 import build_protector
from pgw.pd13.shapes import shapes as l2_shapes
from pgw.detect.recognizers import detect_d2
def sig(v):
    s = "".join("9" if ch.isdigit() else ("a" if ch.isascii() and ch.isalpha() else ("U" if ch.isalpha() else ch)) for ch in v)
    return re.sub(r"a{2,}", "a+", re.sub(r"9{2,}", lambda m: f"9{{{len(m.group())}}}", s))[:40]
prot = build_protector(Path("eval/data3"), ner="en_core_web_lg", l3=True, today=datetime.date(2026, 10, 1))
c = collections.defaultdict(collections.Counter); fam = collections.Counter()
for it in load(Path("/home/user/v3-unsealed/out/test3.jsonl")):
    t = it["text"]; out = prot.protect([{"role": "user", "content": t}], CTX).messages[0]["content"]; of = _fold(out)
    for g in it["spans"]:
        if g["type"] in TAXONOMY and g["type"] != "PERSON":
            v = t[g["start"]:g["end"]]
            if len(v) >= 4 and complete_exposed(v, g["type"], out, of) and not detect_d2(v) and not l2_shapes(v):
                c[g["type"]][sig(v)] += 1; fam[it["family"]] += 1
for ty, cc in c.items(): print(ty, cc.most_common(6))
print("families:", fam.most_common(8))
