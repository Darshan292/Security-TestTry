"""ANALYSIS ONLY: per adversarial kind, which canonicalisation family recovers the original gold value surface
(ceiling; ignores boundary finding). Families tried in order: U unicode, D decoders, S structural."""
import collections, email.header, html, json, quopri, random, re, sys, unicodedata, urllib.parse, importlib.util
sys.path.insert(0, "/home/user/security-testtry")
from eval.run_det import load
from pgw.normalize import normalize
from pathlib import Path
spec = importlib.util.spec_from_file_location("k", "/home/user/v3-unsealed/adversarial/all_kinds.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
items = [it for it in load(Path("/home/user/v3-unsealed/out/test3.jsonl")) if it.get("slice") != "negative"]

def key(s): return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()
def U(s):
    s = normalize(s)[0]                              # existing D0 base: NFKC, confusables, zero-width, native digits
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cf", "Mn"))
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in s)
def D(s):
    for f in (html.unescape, urllib.parse.unquote,
              lambda x: quopri.decodestring(x.encode("latin-1", "ignore")).decode("utf-8", "ignore"),
              lambda x: "".join((p.decode(c or "utf-8", "ignore") if isinstance(p, bytes) else p) for p, c in email.header.decode_header(x)),
              lambda x: x.encode("cp1252", "ignore").decode("utf-8", "ignore"),
              lambda x: x.encode("utf-8").decode("unicode_escape", "ignore") if "\\" in x else x):
        try: s = f(s)
        except Exception: pass
    return s
def S(s):
    s = re.sub(r"<[^>]{0,80}>", "", s)
    return s
FAM = {"U": U, "D": lambda s: U(D(s)), "S": lambda s: U(D(S(s))), "S+rev": lambda s: U(D(S(s)))[::-1]}
out = {}
for k, sp in sorted(m.KINDS.items()):
    rng = random.Random(f"v3-adv|{k}"); c = collections.Counter()
    for it in items:
        y = sp["fn"](it, rng)
        if y is None: continue
        if len(y["spans"]) != len(it["spans"]): c["span_count_changed"] += 1; continue
        for g0, g1 in zip(it["spans"], y["spans"]):
            v0 = it["text"][g0["start"]:g0["end"]]; v1 = y["text"][g1["start"]:g1["end"]]
            if v0 == v1: c["unchanged_value(context-only change)"] += 1; continue
            hit = next((f for f, fn in FAM.items() if key(fn(v1)) == key(v0)), None)
            c[f"recovered_by_{hit}" if hit else "not_recovered"] += 1
    out[k] = {"cls": sp["cls"], **dict(c)}
    print(k, sp["cls"], dict(c))
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
