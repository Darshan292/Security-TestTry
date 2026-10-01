"""Root-cause attribution of leaked gold characters on a frozen split (analysis only, no tuning).

    python -m eval.root_cause --split test2 [--ner en_core_web_lg] [--out eval/results/root_cause_r2.json]

Two attributions are produced for every leaked gold character:
  * automatic: ordered rule (partial-boundary > propagation > person > lexical > structure/label > prose)
  * manual: template-level adjudication (MANUAL below), a human judgement recorded as a DECISION.
Raw values are never written to the output (the corpus contains realistic fake secrets).
"""
from __future__ import annotations

import argparse, collections, json, math, re
from pathlib import Path

from eval.run_det import CTX, build_protector, load
from pgw.alias import canon
from pgw.detect.recognizers import detect_d2, verhoeff_valid
from pgw.types import EntityType

LABEL = {"AADHAAR": "Aadhaar number: ", "DATE_OF_BIRTH": "Date of birth: ", "SECRET": "password: ",
         "COMPENSATION": "Monthly salary: ", "BANK_ACCOUNT": "Bank account number: ", "PHONE": "Mobile: ",
         "EMAIL": "Email: ", "PAN": "PAN: ", "EMPLOYEE_ID": "Employee ID: ", "UPI_ID": "UPI ID: ",
         "CARD": "Card number: ", "IFSC": "IFSC: "}
CONCEPT = {"AADHAAR": r"aadh?aa?r|\buid\b|national|identity|\bid ?(no|num)|icnum|govt|kyc",
           "DATE_OF_BIRTH": r"birth|\bdob\b|d\.o\.b|gbdat|born|janm",
           "SECRET": r"pass|secret|token|key|cred|auth|signature|webhook|hook",
           "COMPENSATION": r"salary|pay|ctc|comp|amount|gross|net|bonus|wage|betrg|lgart|fixed|stipend",
           "BANK_ACCOUNT": r"acc(oun)?t|a/c|bank|bankn|iban|benef"}
STRUCT = re.compile(r"(\t|\||;|,.*,|^\s*[\w .\-/]{1,40}\s*[:=]\s|<\w[^>]*>|\{|\}|\"\w+\"\s*:)")

# Template-level adjudication (DECISION, 2026-10-01): which root cause explains the template's leak.
# Keys are (template_id, type) or (template_id, "*"). Unlisted PERSON -> external-person.
MANUAL = {
    ("yaml_app_config", "SECRET"): "missing-schema-knowledge",          # slack_webhook: URL-path token
    ("smtp_and_webhook_conf", "SECRET"): "missing-schema-knowledge",    # webhook.hr-alerts=
    ("upi_callback_log", "SECRET"): "missing-schema-knowledge",         # "signature key="
    ("vcard_contact_export", "*"): "missing-schema-knowledge",          # RFC 6350 FN/N/BDAY
    ("sap_infotype_dump", "*"): "missing-schema-knowledge",             # GBDAT/ANSAL/ICNUM
    ("kv_pipe_export", "*"): "missing-schema-knowledge",                # D_O_B / GOV_ID keys
    ("positional_flatfile_records", "AADHAAR"): "unseen-structure",     # unlabelled positional records
    ("positional_flatfile_records", "DATE_OF_BIRTH"): "unseen-structure",
    ("long_form_top_label", "*"): "unseen-structure",                   # label on its own line, value below
    ("chat_paste_credentials", "SECRET"): "missing-semantic-context",   # "the api key ... is <hex>"
    ("dob_fix_request", "DATE_OF_BIRTH"): "missing-semantic-context",   # "it should be <date>"
    ("attrition_review_mail", "COMPENSATION"): "missing-semantic-context",
    ("csv_employee_master", "*"): "detector-parser-bug",                # quoted commas
    ("csv_bank_advice_semicolon", "*"): "detector-parser-bug",
    ("csv_candidate_pipeline", "*"): "detector-parser-bug",             # mixed tab/comma, ambiguity -> nothing
    ("markdown_table_paste", "*"): "detector-parser-bug",
}


def manual_cat(tpl: str, ty: str, auto: str) -> str:
    if auto == "unseen-lexical-form":   # the value itself is not parseable even when labelled: lexical wins
        return auto
    if (tpl, ty) in MANUAL:
        return MANUAL[(tpl, ty)]
    if (tpl, "*") in MANUAL:
        return MANUAL[(tpl, "*")]
    if ty == "PERSON":
        return "external-person"
    return "detector-parser-bug" if auto.startswith("bug:") else auto


# Analysis-only ceiling (NOT a detector, not wired into pgw): would a context-free value-shape rule have
# caught the leaked span? Used to separate "needs context to FIND the value" from "needs context only to
# DECIDE about a value whose shape is already suspicious".
_DATE = re.compile(r"\b(\d{1,2}[-/. ](\d{1,2}|[A-Za-z]{3,9})[-/. ](19|20)\d{2}|(19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|(19|20)\d{6})\b")
_TOK = re.compile(r"[A-Za-z0-9_\-+/=]{16,}")


def _entropic(s: str) -> bool:
    c = collections.Counter(s)
    h = -sum(v / len(s) * math.log2(v / len(s)) for v in c.values())
    return h >= 3.5 and sum(bool(re.search(x, s)) for x in ("[a-z]", "[A-Z]", "[0-9]")) >= 2


def shape_hits(text: str) -> list[tuple[str, int, int]]:
    out = []
    for m in re.finditer(r"(?<!\d)(\d{4}[ \-.]?\d{4}[ \-.]?\d{4})(?!\d)", text):
        d = re.sub(r"\D", "", m.group(1))
        if d[0] not in "01" and verhoeff_valid(d):
            out.append(("VERHOEFF12", m.start(), m.end()))
    out += [("DATE", m.start(), m.end()) for m in _DATE.finditer(text)]
    out += [("HIGH_ENTROPY", m.start(), m.end()) for m in _TOK.finditer(text) if _entropic(m.group())]
    return out


def shape_cost(path: Path) -> dict:
    """Shape hits that overlap no gold span of any type, on a DEV split (cost side of the ceiling)."""
    spans, docs, neg = collections.Counter(), collections.Counter(), collections.Counter()
    n = nneg = 0
    for it in load(path):
        n += 1; isneg = not it["spans"]; nneg += isneg; kinds = set()
        for k, a, b in shape_hits(it["text"]):
            if not any(a < g["end"] and b > g["start"] for g in it["spans"]):
                spans[k] += 1; kinds.add(k)
        for k in kinds:
            docs[k] += 1; neg[k] += isneg
    return {"split": path.stem, "docs": n, "negative_docs": nneg, "fp_spans": dict(spans), "docs_with_fp": dict(docs),
            "negative_docs_with_fp": dict(neg)}


def structured(line: str) -> bool:
    return bool(STRUCT.search(line)) or len(re.findall(r"\d{6,}", line)) >= 2 or bool(re.match(r"^\S{20,}$", line.strip()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="eval/data2")
    ap.add_argument("--split", default="test2")
    ap.add_argument("--ner", default="en_core_web_lg")
    ap.add_argument("--cost-split", default="dev2", help="DEV split for the shape-rule false-positive cost")
    ap.add_argument("--out", default="eval/results/root_cause_r2.json")
    a = ap.parse_args()
    data = Path(a.data)
    prot = build_protector(data, True, ner=a.ner or None)
    roster = {r["full_name"].casefold() for r in load(data / "roster.jsonl")}
    auto_c, man_c, cross, tpl_c, shape_c = (collections.Counter() for _ in range(5))
    total_gold = total_leak = 0
    for it in load(data / f"{a.split}.jsonl"):
        t = it["text"]
        preds = prot.analyze([{"role": "user", "content": t}], CTX)[0]
        surfaces = {canon(p.type, t[p.start:p.end]) for p in preds}
        shapes = shape_hits(t)
        for g in it["spans"]:
            s, e, ty = g["start"], g["end"], g["type"]
            total_gold += e - s
            cov = set()
            for p in preds:
                cov.update(range(max(s, p.start), min(e, p.end)))
            leak = (e - s) - len(cov)
            if leak <= 0:
                continue
            total_leak += leak
            v = t[s:e]
            nl = t.find("\n", e)
            line = t[t.rfind("\n", 0, s) + 1: nl if nl >= 0 else len(t)]
            if cov:
                cat = "bug:partial-boundary"
            elif canon(EntityType(ty), v) in surfaces:
                cat = "bug:propagation/resolution"
            elif ty == "PERSON":
                cat = "bug:roster-name-missed" if v.casefold() in roster else "external-person"
            elif not any(d.type.value == ty for d in detect_d2(LABEL.get(ty, "") + v)):
                cat = "unseen-lexical-form"
            else:
                labelled = bool(re.search(CONCEPT.get(ty, r"(?!x)x"), t[max(0, s - 2500):s].casefold()))
                cat = ("missing-schema-knowledge" if labelled else "unseen-structure") if structured(line) \
                    else "missing-semantic-context"
            m = manual_cat(it["template_id"], ty, cat)
            hit = next((k for k, x, y in shapes if x < e and y > s), "none")
            shape_c[(m, ty, hit)] += leak
            auto_c[cat] += leak; man_c[m] += leak; cross[(cat, m)] += leak; tpl_c[(it["template_id"], ty, m)] += leak
    share = lambda c: {k: {"chars": v, "share": round(v / total_leak, 4)} for k, v in c.most_common()}
    out = {"split": a.split, "ner": a.ner, "total_gold_chars": total_gold, "leaked_chars": total_leak,
           "leak_rate": total_leak / total_gold, "automatic": share(auto_c), "manual": share(man_c),
           "auto_x_manual": {f"{x} -> {y}": n for (x, y), n in cross.most_common()},
           "shape_ceiling": [{"manual": m, "type": ty, "shape": h, "chars": n} for (m, ty, h), n in shape_c.most_common()],
           "shape_cost_dev": shape_cost(data / f"{a.cost_split}.jsonl") if a.cost_split else None,
           "by_template": [{"template": tp, "type": ty, "manual": m, "chars": n} for (tp, ty, m), n in tpl_c.most_common()]}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("leaked_chars", "leak_rate", "automatic", "manual")}, indent=1))


if __name__ == "__main__":
    main()
