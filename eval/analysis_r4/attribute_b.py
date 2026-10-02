"""ANALYSIS ONLY (R4 architecture review, spent test3; no tuning). Attribute every CSVE-exposed gold value of arm B
to a root-cause class, then compute counterfactual CSVE / CSVE-HIGH if a class were perfectly fixed."""
import collections, datetime, html, json, re, sys, unicodedata, urllib.parse
from bisect import bisect_left
from pathlib import Path
sys.path.insert(0, "/home/user/security-testtry")
from eval.run_det import CTX, load
from eval.run_v3 import TAXONOMY, HIGH, _fold, complete_exposed
from pgw.pd13 import build_protector
import pgw.pd13.protector as P
from pgw.pd13.shapes import shapes as l2_shapes
from pgw.detect.recognizers import detect_d2
from pgw.normalize import normalize

CAP = []
_orig = P.l4.apply
def hook(dets, shapes, graph, l3, text, *a, **k):
    CAP.append((dets, shapes, graph, l3, text)); return _orig(dets, shapes, graph, l3, text, *a, **k)
P.l4.apply = hook

def nonlatin(v):
    return any(ch.isalpha() and "LATIN" not in unicodedata.name(ch, "") for ch in v)

def canon(v):
    v = unicodedata.normalize("NFKC", v)
    v = "".join(ch for ch in v if unicodedata.category(ch) != "Cf")
    v = html.unescape(urllib.parse.unquote(v))
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in v)

TYPE_OF_SHAPE = {"DATE": "DATE_OF_BIRTH", "AMOUNT": "COMPENSATION", "LONG_DIGITS": "BANK_ACCOUNT", "HIGH_ENTROPY": "SECRET"}
def fires(v, ty):
    if any(d.type.value == ty and (d.end - d.start) >= 0.6 * len(v) for d in detect_d2(v)): return True
    return any((s.end - s.start) >= 0.6 * len(v) for s in l2_shapes(v))   # any shape: value is shape-detectable in isolation

prot = build_protector(Path("/home/user/security-testtry/eval/data3"), ner="en_core_web_lg", l3=True, today=datetime.date(2026, 10, 1))
items = load(Path("/home/user/v3-unsealed/out/test3.jsonl"))
cls_count = collections.Counter(); cls_type = collections.Counter(); docs = []
for it in items:
    t = it["text"]; CAP.clear()
    out = prot.protect([{"role": "user", "content": t}], CTX).messages[0]["content"]; of = _fold(out)
    cap = next((c for c in CAP if c[4] == normalize(t)[0]), CAP[-1] if CAP else None)
    base, om = normalize(t)
    tb = lambda s: bisect_left(om, s, 0, len(base))
    exposed = []
    for g in it["spans"]:
        if g["type"] not in TAXONOMY: continue
        v = t[g["start"]:g["end"]]
        if len(v) < 4 or not complete_exposed(v, g["type"], out, of): continue
        a, b = tb(g["start"]), tb(g["end"])
        dets, shapes, graph, l3, _ = cap
        ov = lambda x0, x1: x0 < b and x1 > a
        in_field = any(ov(f.start, f.end) for f in graph.fields)
        if g["type"] == "PERSON":
            det = any(d.type.value == "PERSON" and ov(d.start, d.end) for d in dets)
            c = ("PERSON:detected_not_masked" if det else
                 "PERSON:nonlatin" if nonlatin(v) else
                 "PERSON:in_field" if in_field else "PERSON:prose")
        else:
            det = [d for d in dets if ov(d.start, d.end)]
            shp = [s for s in shapes if ov(s.start, s.end)]
            rel = l3 is not None and any(ov(x, y) for x, y, _ in l3.released)
            if det: c = "detected_not_masked(policy/resolution)"
            elif shp and rel: c = "L3_release"
            elif shp: c = "anchoring_scope(not person-bearing)"
            elif fires(v, g["type"]): c = "parsing_segmentation(shaped in isolation, missed in context)"
            elif fires(canon(v), g["type"]): c = "normalization(shaped after canonicalisation)"
            else: c = "shape_gap(unshaped form)"
        exposed.append((c, g["type"]))
        cls_count[c] += 1; cls_type[(c, g["type"])] += 1
    docs.append(exposed)

n = len(items)
def csve(fixed, high=False):
    return sum(1 for ex in docs if any(c not in fixed and (not high or ty in HIGH) for c, ty in ex)) / n
allc = sorted(cls_count)
res = {"exposed_values_by_class": dict(cls_count.most_common()),
       "by_class_type": {f"{c} | {ty}": v for (c, ty), v in sorted(cls_type.items())},
       "baseline": {"csve": csve(set()), "csve_high": csve(set(), True)},
       "fix_one_class": {c: {"csve": csve({c}), "csve_high": csve({c}, True)} for c in allc}}
groups = {"stronger_PERSON": {c for c in allc if c.startswith("PERSON")},
          "normalization": {c for c in allc if c.startswith("normalization")},
          "parsing": {c for c in allc if c.startswith("parsing")},
          "value_shape": {c for c in allc if c.startswith("shape_gap")},
          "anchoring_policy": {c for c in allc if c.startswith(("anchoring", "L3_release", "detected_not"))}}
res["fix_group"] = {k: {"csve": csve(v), "csve_high": csve(v, True)} for k, v in groups.items()}
res["fix_all_but_PERSON"] = {"csve": csve(set(allc) - groups["stronger_PERSON"]), "csve_high": csve(set(allc) - groups["stronger_PERSON"], True)}
Path(sys.argv[1]).write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
