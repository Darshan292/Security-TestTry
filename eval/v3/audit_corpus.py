"""Corpus v3 validators and contamination audit (CONTAMINATION C2, C8; SPEC §3-§7). Aggregates only.

    python eval/v3/audit_corpus.py vocab                       # v1/v2 key vocabulary -> eval/data3/v12_keys.json
    python eval/v3/audit_corpus.py check --split-file F ... --families DIR [--roster R]
Never prints item text, family ids of sealed splits are replaced by opaque indexes.
"""
import argparse, ast, collections, hashlib, json, re, sys, unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OLD = [REPO / "eval/data" / f for f in ("dev.jsonl", "test.jsonl", "tune.jsonl")] + \
      [REPO / "eval/data2" / f for f in ("dev2.jsonl", "test2.jsonl", "ner_test2.jsonl")]
OLD_ROSTERS = [REPO / "eval/data/roster.jsonl", REPO / "eval/data2/roster.jsonl"]
TAX = {"PERSON", "EMAIL", "PHONE", "AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC", "CARD", "UPI_ID", "SECRET",
       "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH"}


def norm_key(k):
    return "".join(ch for ch in k.casefold() if unicodedata.category(ch)[0] in "LMN")


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def keys_in(text):
    ks = set()
    ks |= {m.group(1) for m in re.finditer(r'"([^"\n]{1,48})"\s*:', text)}
    ks |= {m.group(1) for m in re.finditer(r"(?m)(?:^|[\s,;|{(])([A-Za-z_][\w .\-/]{0,40}?)\s*[:=]", text)}
    ks |= {m.group(1) for m in re.finditer(r"</?([A-Za-z_][\w:.\-]*)", text)}
    first = text.split("\n", 1)[0]
    for d in ("\t", "|", ";", ","):
        if first.count(d) >= 2:
            ks |= {c.strip(' "') for c in first.split(d) if 0 < len(c.strip(' "')) <= 40}
    return {norm_key(k) for k in ks if norm_key(k)}


def old_items():
    for p in OLD:
        if p.exists():
            yield from load(p)


def vocab():
    v = set()
    for it in old_items():
        v |= keys_in(it["text"])
    out = REPO / "eval/data3/v12_keys.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(sorted(v), ensure_ascii=False))
    print(f"v1/v2 key vocabulary: {len(v)} normalised keys -> {out}")


def ngrams(text, n=8):
    w = re.findall(r"\w+", text.casefold())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def verhoeff_ok(num):
    d = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],
         [5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
    p = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],
         [4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = d[c][p[i % 8][int(ch)]]
    return c == 0


def luhn_ok(num):
    s = 0
    for i, ch in enumerate(reversed(num)):
        x = int(ch) * (2 if i % 2 else 1)
        s += x - 9 if x > 9 else x
    return s % 10 == 0


def to_ascii_digits(s):
    return "".join(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in s)


def validity(t, v):
    if t == "AADHAAR":
        d = re.sub(r"\D", "", to_ascii_digits(v))
        return len(d) == 12 and d[0] not in "01" and verhoeff_ok(d)
    if t == "CARD":
        d = re.sub(r"\D", "", to_ascii_digits(v))
        return 13 <= len(d) <= 19 and luhn_ok(d)
    if t == "PAN":
        return bool(re.fullmatch(r"[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]", v.upper()))
    if t == "IFSC":
        return bool(re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v.upper()))
    return True


def check(split_files, fam_dir, roster):
    errs = collections.Counter()
    splits = {Path(f).stem: load(f) for f in split_files}
    # structural validity per item
    vals_by_split = collections.defaultdict(set); ext_tokens = collections.defaultdict(set)
    fam_stats = collections.defaultdict(lambda: collections.Counter())
    skel_chars = collections.defaultdict(collections.Counter)
    for sp, items in splits.items():
        split_chars = 0
        for it in items:
            t = it["text"]; spans = it["spans"]; f = it["family"]
            fam_stats[(sp, f)]["items"] += 1
            fam_stats[(sp, f)]["neg" if it["slice"] == "negative" else "pos"] += 1
            if it["slice"] == "negative" and spans: errs["negative_with_spans"] += 1
            if it["slice"] != "negative" and not spans: errs["positive_without_spans"] += 1
            last = 0
            for g in spans:
                s, e, ty = g["start"], g["end"], g["type"]
                if not (0 <= s < e <= len(t)): errs["span_out_of_bounds"] += 1; continue
                if s < last: errs["span_overlap_or_unsorted"] += 1
                last = e
                if ty not in TAX and ty != "OOT": errs["unknown_type"] += 1
                v = t[s:e]
                if v != v.strip(): errs["span_has_outer_whitespace"] += 1
                if not validity(ty, v): errs[f"invalid_{ty}"] += 1
                fam_stats[(sp, f)][f"type:{ty}"] += 1
                skel_chars[(sp, f)][it["template_id"]] += e - s
                split_chars += e - s
                if ty != "PERSON":
                    vals_by_split[sp].add((ty, v))
                elif it.get("name_source") in ("external", "mixed"):
                    ext_tokens[sp] |= {w.casefold() for w in re.findall(r"\w+", v) if len(w) > 1}
            for g in spans:     # unlabelled re-occurrence of a planted value inside the same item
                v = t[g["start"]:g["end"]]
                if len(v) >= 6:
                    occ = [m.start() for m in re.finditer(re.escape(v), t)]
                    lab = {x["start"] for x in spans}
                    errs["unlabelled_reoccurrence"] += sum(1 for o in occ if o not in lab and
                                                           not any(x["start"] <= o < x["end"] for x in spans))
        fam_stats[(sp, "__split__")]["gold_chars"] = split_chars
    sp_names = sorted(splits)
    for i, a in enumerate(sp_names):
        for b in sp_names[i + 1:]:
            if a[:4] == b[:4] == "dev3":
                continue
            errs[f"identical_values_{a}_vs_{b}"] += len(vals_by_split[a] & vals_by_split[b])
            errs[f"external_name_tokens_shared_{a}_vs_{b}"] += len(ext_tokens[a] & ext_tokens[b])
    # caps and per-family requirements
    caps = collections.Counter()
    for (sp, f), c in fam_stats.items():
        if f == "__split__":
            continue
        gold = sum(skel_chars[(sp, f)].values())
        if c["pos"] != 60 or c["neg"] != 15: caps["family_item_count_not_60_15"] += 1
        if len(skel_chars[(sp, f)]) < 6: caps["family_lt_6_skeletons_with_gold"] += 1
        tax_types = {k[5:] for k in c if k.startswith("type:") and k[5:] in TAX}
        if len(tax_types) < 3: caps["family_lt_3_taxonomy_types"] += 1
        if "PERSON" not in tax_types: caps["family_without_person"] += 1
        if gold and max(skel_chars[(sp, f)].values()) > 0.25 * gold: caps["skeleton_gt_25pct_of_family"] += 1
        if gold > 0.06 * fam_stats[(sp, "__split__")]["gold_chars"]: caps["family_gt_6pct_of_split"] += 1
    # contamination vs v1/v2
    old_vals, old_names, old_grams = set(), set(), set()
    for it in old_items():
        for g in it["spans"]:
            v = it["text"][g["start"]:g["end"]]
            (old_names if g["type"] == "PERSON" else old_vals).add(v.casefold() if g["type"] == "PERSON" else v)
        old_grams |= ngrams(it["text"])
    for r in OLD_ROSTERS:
        if r.exists():
            old_names |= {x["full_name"].casefold() for x in load(r)}
    cont = collections.Counter()
    grams_total = grams_old = 0
    keys_v3 = set(); keys_seen = set()
    v12 = set(json.loads((REPO / "eval/data3/v12_keys.json").read_text()))
    for sp, items in splits.items():
        for it in items:
            for g in it["spans"]:
                v = it["text"][g["start"]:g["end"]]
                if g["type"] == "PERSON" and v.casefold() in old_names and len(v.split()) >= 2: cont[f"{sp}:full_name_in_v12"] += 1
                if g["type"] != "PERSON" and v in old_vals: cont[f"{sp}:value_in_v12"] += 1
                if g.get("key"):
                    k = norm_key(g["key"]); keys_v3.add(k); keys_seen |= {k} & v12
            gs = ngrams(it["text"]); grams_total += len(gs); grams_old += len(gs & old_grams)
    if roster:
        rn = {x["full_name"].casefold() for x in load(roster)}
        cont["roster_full_names_in_v12"] = len(rn & old_names)
    # C2 imports
    imports = collections.Counter()
    if fam_dir:
        for p in Path(fam_dir).rglob("*.py"):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                       [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                for m in mods:
                    top = m.split(".")[0]
                    if top in ("eval", "pgw", "tests") or (top not in sys.stdlib_module_names and top not in ("common", "families", "adversarial")):
                        imports[f"non_stdlib_import:{top}"] += 1
    report = {"items": {sp: len(v) for sp, v in splits.items()}, "errors": dict(errs), "family_requirements": dict(caps),
              "families": {sp: len({f for (s, f) in fam_stats if s == sp and f != "__split__"}) for sp in splits},
              "contamination": dict(cont), "v3_8gram_share_in_v12": grams_old / grams_total if grams_total else None,
              "keys": {"v3_distinct": len(keys_v3), "seen_in_v12": len(keys_seen),
                       "unseen_share": 1 - len(keys_seen) / len(keys_v3) if keys_v3 else None},
              "imports": dict(imports)}
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("vocab", "check"))
    ap.add_argument("--split-file", nargs="*", default=[])
    ap.add_argument("--families")
    ap.add_argument("--roster")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "vocab":
        vocab()
    else:
        r = check(a.split_file, a.families, a.roster)
        if a.out:
            Path(a.out).write_text(json.dumps(r, indent=1, ensure_ascii=False))
