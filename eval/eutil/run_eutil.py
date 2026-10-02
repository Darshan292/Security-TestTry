"""E-UTIL harness (EVALUATION_PLAN.md, amendment 2026-10-02): usefulness of protected prompts.

    python eval/eutil/run_eutil.py prepare --condition {raw,gateway,max}
    python eval/eutil/run_eutil.py finish  --condition C      # needs out/answers_C.jsonl
    python eval/eutil/run_eutil.py score                      # all conditions with final files

Scoring is deterministic (no LLM judge). Only gateway-added latency (protect + restore) is measured.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pgw.detect.edm import RosterIndex  # noqa: E402
from pgw.pipeline import Keys, Protector  # noqa: E402
from pgw.policy import PolicyPack  # noqa: E402
from pgw.stream import Restorer  # noqa: E402
from pgw.types import Action, BlockedRequest, Context, Entity, EntityType  # noqa: E402

EUTIL = ROOT / "eval" / "eutil"
OUT = EUTIL / "out"
POLICY = ROOT / "policies" / "hris-default.yaml"
TEST_KEY = b"eutil-fixed-test-key-0123456789"[:30].ljust(32, b"!")
CONV_ID = "eutil-fixed-conversation"
MAX_PURPOSE = "eval_tokenize_all"
CONDITIONS = ("raw", "gateway", "max")
SYSTEM_NOTE = ("Tokens in square brackets such as [PERSON_AB12CD] are placeholders for confidential values; "
               "keep them exactly as written where you need to refer to them.")
PLACEHOLDER_RE = re.compile(r"\[[A-Z_]+_[0-9A-Z]+\]")
VALUE_RE = re.compile(r"\[VALUE_(\d+)\]", re.IGNORECASE)

# --- MAX second pass ----------------------------------------------------------------------------

_TYPES = "|".join(sorted((t.value for t in EntityType), key=len, reverse=True))
# anything bracket-shaped that the pipeline emitted: [TYPE_xxxxxx] aliases and [TYPE] redactions
_PROTECTED_RE = re.compile(rf"\[(?:{_TYPES})(?:_[0-9A-Z]+)?\]|\[VALUE_\d+\]")
_TOKEN_RE = re.compile(r"[₹$]?\w+(?:[.,:/@'’\-]\w+)*")

STOPLIST = frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
i i'm i've i'll i'd a an the and or but if so as at by for from in into of on to with without about over under
this that these those there here it its he she they we you your our their his her them us me my
is are was were be been being am do does did done have has had will would shall should can could may might must
not no yes ok okay please thanks thank regards dear hi hello hey best sincerely cheers sir madam
what when where which who whom whose why how all any each every some none both either neither
also then than too very just only more most less least many much few other another such same own
after before during between through against above below up down out off again further once
hr hris ctc kpi ceo cto cfo coo vp it qa pm ui ux id ids pip fy q1 q2 q3 q4 ok faq fyi eod asap
team teams manager managers employee employees staff department departments company office
employer employee's manager's hrbp recruiter recruiters candidate candidates
case cases summary summarise summarize draft email emails message ticket tickets request requests
issue issues complaint complaints leave policy policies review reviews report reports note notes
salary salaries compensation band bands grade level levels role roles title position
payroll benefits bonus bonuses increment promotion promotions appraisal performance
meeting meetings follow followup update updates action actions item items step steps
task tasks instruction instructions context output format label labels json text
subject body regards from to cc bcc date sent re fwd
low medium high critical urgent normal priority category status open closed pending resolved
hr_case_summary email_draft ticket_classification comp_band_analysis
confidential internal public restricted
india indian rupees rupee inr lakh lakhs crore crores lpa
""".split())


def _sentence_initial(text: str, pos: int) -> bool:
    j = pos
    while j > 0 and text[j - 1] in " \t":
        j -= 1
    if j == 0:
        return True
    prev = text[j - 1]
    return prev in ".!?:\n\r-*#>•|\"'“(—–" or (prev == "]" and False)


def _digit_or_cap(tok: str, text: str, pos: int) -> bool:
    if any(c.isdigit() for c in tok):
        return True
    if not tok[0].isupper():
        return False
    if _sentence_initial(text, pos):
        return False
    return tok.casefold() not in STOPLIST


def max_transform(text: str, reverse: dict[str, str], forward: dict[str, str]) -> str:
    """Replace digit-bearing tokens and non-initial capitalised tokens outside STOPLIST with [VALUE_n].

    reverse: "VALUE_n" -> original; forward: original -> "VALUE_n" (both shared across messages of an item
    so numbering is deterministic in order of first appearance). Existing [TYPE_xxxxxx] aliases are untouched.
    """
    out: list[str] = []
    last = 0
    protected = [(m.start(), m.end()) for m in _PROTECTED_RE.finditer(text)]
    pi = 0

    def plain(seg_start: int, seg_end: int) -> str:
        seg = text[seg_start:seg_end]
        res, k = [], 0
        for m in _TOKEN_RE.finditer(seg):
            tok = m.group(0)
            if _digit_or_cap(tok, text, seg_start + m.start()):
                name = forward.get(tok)
                if name is None:
                    name = forward[tok] = f"VALUE_{len(forward) + 1}"
                    reverse[name] = tok
                res.append(seg[k:m.start()])
                res.append(f"[{name}]")
                k = m.end()
        res.append(seg[k:])
        return "".join(res)

    for s, e in protected:
        out.append(plain(last, s))
        out.append(text[s:e])
        last = e
    out.append(plain(last, len(text)))
    return "".join(out)


# --- prepare ------------------------------------------------------------------------------------

def build_user_message(item: dict) -> str:
    msg = f"{item['instruction']}\n\n---\n{item['context']}"
    fmt = item.get("output_format", "text")
    if fmt == "json":
        msg += "\n\nRespond with JSON only."
    elif fmt == "label":
        msg += f"\n\nAnswer with exactly one label from: {', '.join(item.get('labels') or [])}. Output the label only."
    return msg


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def build_protector(roster_path: Path | None) -> Protector:
    keys = Keys(TEST_KEY)
    policy = PolicyPack.load(POLICY)
    roster = None
    if roster_path is not None:
        roster = RosterIndex(keys.derive("*", "edm"))   # same key derivation as create_app
        for rec in load_jsonl(roster_path):
            roster.add_employee(rec)
    return Protector(policy, keys, roster, None)         # no ledger, no NER, as create_app's roster path


def _alias_dump(alias_map: dict) -> dict:
    return {k: {"type": e.type.value, "value": e.value, "action": e.action.value} for k, e in alias_map.items()}


def alias_load(d: dict) -> dict:
    return {k: Entity(EntityType(v["type"]), v["value"], Action(v.get("action", "TOKENIZE")), sources={v["value"]})
            for k, v in d.items()}


def count_leaks(item: dict, prompt: str | None) -> tuple[int, int]:
    if prompt is None:
        return 0, 0
    sens = sum(1 for s in item.get("sensitive") or [] if s["value"] in prompt)
    crit = sum(1 for v in item.get("task_critical_nonsensitive") or [] if v not in prompt)
    return sens, crit


def prepare_item(item: dict, condition: str, protector: Protector | None) -> tuple[dict, dict]:
    user = build_user_message(item)
    rec = {"id": item["id"], "task_type": item["task_type"], "condition": condition, "purpose": item["purpose"],
           "blocked": False, "blocked_types": [], "system": None, "prompt": user, "protect_ms": 0.0}
    restore = {"alias_map": {}, "reverse": {}, "purpose": item["purpose"]}
    if condition != "raw":
        purpose = item["purpose"] if condition == "gateway" else MAX_PURPOSE
        ctx = Context("eval", "eutil", purpose, CONV_ID, True)
        msgs = [{"role": "user", "content": user}]
        t0 = time.perf_counter()
        try:
            prot = protector.protect(msgs, ctx)
            text = prot.messages[0]["content"]
            rev: dict[str, str] = {}
            if condition == "max":
                text = max_transform(text, rev, {})
            rec["protect_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            rec["prompt"], rec["system"] = text, SYSTEM_NOTE
            rec["entity_counts"] = prot.type_counts()
            restore = {"alias_map": _alias_dump(prot.alias_map), "reverse": rev, "purpose": item["purpose"]}
        except BlockedRequest as e:
            rec.update(blocked=True, blocked_types=list(e.types), prompt=None, system=None,
                       protect_ms=round((time.perf_counter() - t0) * 1000, 3))
        except Exception as e:   # unknown purpose etc.: the product would refuse; record, never forward
            rec.update(blocked=True, blocked_types=[f"ERROR:{type(e).__name__}"], prompt=None, system=None,
                       protect_ms=round((time.perf_counter() - t0) * 1000, 3))
    rec["sensitive_in_prompt"], rec["critical_masked"] = count_leaks(item, rec["prompt"])
    return rec, restore


def cmd_prepare(a) -> None:
    tasks = load_jsonl(a.tasks)
    protector = None
    if a.condition != "raw":
        protector = build_protector(Path(a.roster) if a.roster else None)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    recs, restores = [], {}
    for item in tasks:
        r, rs = prepare_item(item, a.condition, protector)
        recs.append(r)
        restores[item["id"]] = rs
    (out / f"prompts_{a.condition}.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    (out / f"restore_{a.condition}.json").write_text(json.dumps(restores, ensure_ascii=False, indent=1), encoding="utf-8")
    nb = sum(r["blocked"] for r in recs)
    print(f"{a.condition}: {len(recs)} items, {nb} blocked, "
          f"{sum(r['sensitive_in_prompt'] for r in recs)} sensitive values in prompts, "
          f"{sum(r['critical_masked'] for r in recs)} critical values masked")


# --- finish -------------------------------------------------------------------------------------

def restore_answer(answer: str, rs: dict, purpose: str, condition: str, policy: PolicyPack,
                   roster: RosterIndex | None) -> tuple[str, int]:
    if condition == "raw":
        return answer, 0
    r = Restorer(alias_load(rs.get("alias_map", {})), restore=True, never_restore=policy.never_restore,
                 roster=roster, mode=policy.stream_mode(purpose))
    text = r.feed(answer) + r.finish()
    if condition == "max":
        rev = rs.get("reverse", {})
        text = VALUE_RE.sub(lambda m: rev.get(f"VALUE_{m.group(1)}", m.group(0)), text)
    return text, len(r.events)


def cmd_finish(a) -> None:
    out = Path(a.out)
    prompts = {r["id"]: r for r in load_jsonl(out / f"prompts_{a.condition}.jsonl")}
    restores = json.loads((out / f"restore_{a.condition}.json").read_text(encoding="utf-8"))
    answers = {r["id"]: r["answer"] for r in load_jsonl(out / f"answers_{a.condition}.jsonl")}
    policy = PolicyPack.load(POLICY)
    roster = None
    if a.condition != "raw" and a.roster and Path(a.roster).exists():
        roster = build_protector(Path(a.roster)).roster
    finals = []
    for id_, p in prompts.items():
        rec = {"id": id_, "task_type": p["task_type"], "condition": a.condition, "blocked": p["blocked"],
               "blocked_types": p["blocked_types"], "protect_ms": p["protect_ms"],
               "sensitive_in_prompt": p["sensitive_in_prompt"], "critical_masked": p["critical_masked"],
               "answer": answers.get(id_), "restored": None, "restore_ms": 0.0, "unrestored": [],
               "fabricated": [], "gate_events": 0, "missing_answer": False}
        if p["blocked"]:
            pass
        elif id_ not in answers:
            rec["missing_answer"] = True
        else:
            ans = answers[id_]
            t0 = time.perf_counter()
            text, ev = restore_answer(ans, restores[id_], p.get("purpose", ""), a.condition, policy, roster)
            rec["restore_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            rec["restored"], rec["gate_events"] = text, ev
            rec["unrestored"] = PLACEHOLDER_RE.findall(text)
            pset = {m.upper() for m in PLACEHOLDER_RE.findall(p["prompt"] or "")}
            rec["fabricated"] = sorted({m for m in PLACEHOLDER_RE.findall(ans) if m.upper() not in pset and m.upper() != "[PERSON_AB12CD]"}
                                       ) if a.condition != "raw" else []
        finals.append(rec)
    (out / f"final_{a.condition}.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in finals), encoding="utf-8")
    print(f"{a.condition}: wrote {len(finals)} final records "
          f"({sum(r['missing_answer'] for r in finals)} missing answers)")


# --- scoring ------------------------------------------------------------------------------------

def fold(s) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(s)).casefold().split())


_NUM_RE = re.compile(r"\d(?:[\d,.]*\d)?")


def digits(s) -> str:
    if isinstance(s, float) and s.is_integer():
        s = int(s)
    return re.sub(r"\D", "", str(s))


def text_numbers(text: str) -> set[str]:
    return {digits(m.group(0)) for m in _NUM_RE.finditer(text or "")}


def found_fact(fact, text: str) -> bool:
    return fold(fact) in fold(text or "")


def found_number(value, text: str) -> bool:
    d = digits(value)
    if not d:
        return found_fact(value, text)
    return d in text_numbers(text)


def fact_hits(gold: dict, text: str) -> dict[str, bool]:
    hits = {}
    for f in gold.get("facts") or []:
        hits[f"fact:{f}"] = found_fact(f, text)
    for k, v in (gold.get("numbers") or {}).items():
        hits[f"num:{k}"] = found_number(v, text)
    return hits


def parse_json_answer(answer: str):
    s = (answer or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    cands = [m.group(1).strip()] if m else []
    cands.append(s)
    dec = json.JSONDecoder()
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            pass
        for i, ch in enumerate(c):
            if ch in "{[":
                try:
                    return dec.raw_decode(c[i:])[0]
                except ValueError:
                    continue
    return None


def json_match(g, p) -> bool:
    if isinstance(g, dict):
        return isinstance(p, dict) and all(k in p and json_match(v, p[k]) for k, v in g.items())
    if isinstance(g, list):
        if not isinstance(p, list) or len(g) != len(p):
            return False
        rest = list(p)
        for x in g:
            for y in rest:
                if json_match(x, y):
                    rest.remove(y)
                    break
            else:
                return False
        return True
    if isinstance(g, bool) or g is None:
        return g == p
    if isinstance(g, (int, float)) or (isinstance(g, str) and re.fullmatch(r"[\d,.\s₹$-]+", g.strip() or "x")):
        if isinstance(p, (int, float, str)) and not isinstance(p, bool):
            return digits(g) != "" and digits(g) == digits(p)
        return False
    return not isinstance(p, (dict, list)) and fold(g) == fold(p)


def _clean_label(s: str) -> str:
    return fold(s).strip(" .\"'`*:;")


def score_item(task: dict, text: str | None) -> dict:
    """Deterministic per-item scores for one answer text (None = no answer)."""
    gold = task.get("gold") or {}
    fmt = task.get("output_format", "text")
    t = text or ""
    if text is None:
        score = 0.0
    elif fmt == "label":
        score = 1.0 if _clean_label(t) == _clean_label(gold.get("label", "")) else 0.0
    elif fmt == "json" and gold.get("json"):
        parsed = parse_json_answer(t)
        gj = gold["json"]
        if not isinstance(parsed, dict):
            score = 0.0
        else:
            score = sum(1 for k, v in gj.items() if k in parsed and json_match(v, parsed[k])) / len(gj)
    else:
        h = fact_hits(gold, t)
        score = sum(h.values()) / len(h) if h else 1.0
    need = 0.999 if fmt in ("label", "json") else 0.8
    ents = gold.get("required_entities") or []
    secs = gold.get("required_sections") or []
    return {
        "score": round(score, 4), "correct": bool(text is not None and score >= need),
        "entity_preservation": (sum(found_fact(e, t) for e in ents) / len(ents)) if ents and text is not None
        else (0.0 if ents else None),
        "section_completeness": (sum(found_fact(s, t) for s in secs) / len(secs)) if secs and text is not None
        else (0.0 if secs else None),
        "forbidden_hits": sum(found_fact(f, t) for f in gold.get("forbidden") or []) if text is not None else 0,
        "fact_hits": fact_hits(gold, t) if text is not None else {},
    }


def pct(vals: list[float], q: float):
    if not vals:
        return None
    v = sorted(vals)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 3)


def mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def load_finals(out: Path) -> dict[str, dict[str, dict]]:
    res = {}
    for c in CONDITIONS:
        p = out / f"final_{c}.jsonl"
        if p.exists():
            res[c] = {r["id"]: r for r in load_jsonl(p)}
    return res


def score_all(tasks: list[dict], finals: dict[str, dict[str, dict]]) -> dict:
    tmap = {t["id"]: t for t in tasks}
    items: dict[str, dict[str, dict]] = {}
    for c, recs in finals.items():
        for id_, r in recs.items():
            task = tmap.get(id_)
            if task is None:
                continue
            blocked = bool(r.get("blocked"))
            text = None if blocked else r.get("restored")
            s = score_item(task, text)
            s.update(blocked=blocked, task_type=task["task_type"], condition=c,
                     critical_masked=r.get("critical_masked", 0), sensitive_in_prompt=r.get("sensitive_in_prompt", 0),
                     unrestored=len(r.get("unrestored") or []), fabricated=len(r.get("fabricated") or []),
                     gate_events=r.get("gate_events", 0),
                     latency_ms=round((r.get("protect_ms") or 0) + (r.get("restore_ms") or 0), 3) if c != "raw" else None,
                     words=len((text or "").split()))
            items.setdefault(id_, {})[c] = s
    for id_, per in items.items():
        raw = per.get("raw")
        raw_hits = {k for k, v in (raw or {}).get("fact_hits", {}).items() if v}
        for c, s in per.items():
            if c == "raw":
                s["factual_equivalence"], s["length_ratio"] = None, None
                continue
            if raw is None or not raw_hits:
                s["factual_equivalence"] = None
            else:
                s["factual_equivalence"] = len([k for k in raw_hits if s["fact_hits"].get(k)]) / len(raw_hits)
            s["length_ratio"] = round(s["words"] / raw["words"], 4) if raw and raw["words"] else None
    return {"items": items, "aggregate": aggregate(items, finals)}


def _agg(rows: list[dict], cond: str) -> dict:
    ub = [r for r in rows if not r["blocked"]]
    return {
        "n": len(rows), "blocked": len(rows) - len(ub),
        "blocked_rate": round((len(rows) - len(ub)) / len(rows), 4) if rows else None,
        "correct_rate_all": mean([1.0 if r["correct"] else 0.0 for r in rows]),
        "correct_rate_unblocked": mean([1.0 if r["correct"] else 0.0 for r in ub]),
        "mean_score_all": mean([r["score"] for r in rows]),
        "mean_score_unblocked": mean([r["score"] for r in ub]),
        "entity_preservation": mean([r["entity_preservation"] if not r["blocked"] else (0.0 if r["entity_preservation"] is not None else None) for r in rows]),
        "factual_equivalence": mean([r["factual_equivalence"] for r in ub]) if cond != "raw" else None,
        "section_completeness": mean([r["section_completeness"] for r in ub]),
        "length_ratio": mean([r["length_ratio"] for r in ub]) if cond != "raw" else None,
        "critical_masked": sum(r["critical_masked"] for r in rows),
        "unrestored": sum(r["unrestored"] for r in rows), "fabricated": sum(r["fabricated"] for r in rows),
        "forbidden_hits": sum(r["forbidden_hits"] for r in rows),
        "sensitive_in_prompt": sum(r["sensitive_in_prompt"] for r in rows),
        "items_with_sensitive_in_prompt": sum(1 for r in rows if r["sensitive_in_prompt"]),
        "latency_p50_ms": pct([r["latency_ms"] for r in rows if r["latency_ms"] is not None], 0.5) if cond != "raw" else None,
        "latency_p95_ms": pct([r["latency_ms"] for r in rows if r["latency_ms"] is not None], 0.95) if cond != "raw" else None,
    }


def _deg(raw, val):
    if raw in (None, 0) or val is None:
        return None
    return round((raw - val) / raw, 4)


def aggregate(items: dict, finals: dict) -> dict:
    agg: dict = {}
    types = sorted({s["task_type"] for per in items.values() for s in per.values()})
    for c in finals:
        rows = [per[c] for per in items.values() if c in per]
        agg[c] = {"overall": _agg(rows, c), "by_task_type": {}}
        for tt in types:
            agg[c]["by_task_type"][tt] = _agg([r for r in rows if r["task_type"] == tt], c)
    if "raw" in agg:
        for c in agg:
            if c == "raw":
                continue
            for key, blk in [("overall", agg[c]["overall"])] + list(agg[c]["by_task_type"].items()):
                rb = agg["raw"]["overall"] if key == "overall" else agg["raw"]["by_task_type"][key]
                blk["degradation_correct_all"] = _deg(rb["correct_rate_all"], blk["correct_rate_all"])
                blk["degradation_correct_unblocked"] = _deg(rb["correct_rate_all"], blk["correct_rate_unblocked"])
                blk["degradation_mean_score_all"] = _deg(rb["mean_score_all"], blk["mean_score_all"])
                d = blk["degradation_correct_all"]
                blk["gate_le_5pct"] = None if d is None else bool(d <= 0.05)
    return agg


def _f(v, pctfmt=False):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pctfmt else f"{v}"


def render_scores_md(res: dict) -> str:
    agg = res["aggregate"]
    L = ["# E-UTIL scores", "", "Deterministic scoring; no LLM judge. Degradation is relative to RAW "
         "(E-UTIL gate: <= 5 %, reported, not enforced). `all` counts blocked items as incorrect; `unblocked` excludes them.", ""]
    cols = [("n", "n"), ("blocked_rate", "blocked"), ("correct_rate_all", "correct (all)"),
            ("correct_rate_unblocked", "correct (unblocked)"), ("mean_score_all", "mean score"),
            ("entity_preservation", "entity pres."), ("factual_equivalence", "fact. equiv."),
            ("section_completeness", "sections"), ("length_ratio", "len ratio"),
            ("critical_masked", "crit masked"), ("unrestored", "unrestored"), ("fabricated", "fabricated"),
            ("forbidden_hits", "forbidden"), ("items_with_sensitive_in_prompt", "items w/ sensitive out"),
            ("degradation_correct_all", "degr. (all)"), ("degradation_correct_unblocked", "degr. (unblocked)"),
            ("gate_le_5pct", "gate <=5%"), ("latency_p50_ms", "p50 ms"), ("latency_p95_ms", "p95 ms")]
    ratio = {"blocked_rate", "correct_rate_all", "correct_rate_unblocked", "mean_score_all", "entity_preservation",
             "factual_equivalence", "section_completeness", "degradation_correct_all", "degradation_correct_unblocked"}

    def table(rows):
        out = ["| " + " | ".join(["group"] + [c[1] for c in cols]) + " |", "|" + "---|" * (len(cols) + 1)]
        for name, r in rows:
            out.append("| " + " | ".join([name] + [_f(r.get(k), k in ratio) for k, _ in cols]) + " |")
        return out

    L += ["## Per condition", ""] + table([(c, agg[c]["overall"]) for c in agg]) + [""]
    types = sorted({t for c in agg for t in agg[c]["by_task_type"]})
    for tt in types:
        L += [f"## Task type: {tt}", ""] + table([(c, agg[c]["by_task_type"][tt]) for c in agg]) + [""]
    return "\n".join(L) + "\n"


def render_review(tasks, finals, prompts, res, n=12) -> str:
    items = res["items"]
    tmap = {t["id"]: t for t in tasks}
    diffs = {}
    for id_, per in items.items():
        if "raw" not in per:
            continue
        d = []
        for c in ("gateway", "max"):
            if c in per:
                d.append(per["raw"]["score"] - per[c]["score"] + (1.0 if per[c]["blocked"] else 0.0)
                         + 0.01 * (per[c]["critical_masked"] + per[c]["fabricated"] + per[c]["unrestored"]))
        diffs[id_] = max(d) if d else 0.0
    order = sorted(diffs, key=lambda i: (-diffs[i], i))
    chosen = order[:n]
    for tt in sorted({tmap[i]["task_type"] for i in order if i in tmap}):
        if not any(tmap[i]["task_type"] == tt for i in chosen):
            chosen.append(next(i for i in order if tmap[i]["task_type"] == tt))
    fence = "~~~~"

    def block(x):
        return f"{fence}\n{x}\n{fence}\n"

    L = ["# E-UTIL review cases", "", "Items with the largest RAW-vs-protected difference (plus at least one per task type). "
         "For human review; scores are deterministic. 'What left' is the outbound prompt.", ""]
    for id_ in chosen:
        t = tmap[id_]
        L += [f"## {id_}  ({t['task_type']}, purpose {t['purpose']}, format {t.get('output_format', 'text')})", "",
              f"Difference score: {diffs[id_]:.3f}", "", "### Instruction", "", block(t["instruction"])]
        L += ["### RAW answer", "", block((finals.get("raw", {}).get(id_) or {}).get("restored") or "(none)")]
        for c in ("gateway", "max"):
            if c not in finals:
                continue
            p, f = prompts.get(c, {}).get(id_, {}), finals[c].get(id_, {})
            L += [f"### {c.upper()} prompt (what left)", ""]
            if p.get("blocked"):
                L += [f"BLOCKED: {', '.join(p.get('blocked_types') or [])}", ""]
            else:
                L += [block(p.get("prompt") or "(missing)")]
            L += [f"### {c.upper()} restored answer", "", block(f.get("restored") or "(none)")]
        L += ["### Scores", "", "| condition | correct | score | entity pres. | fact. equiv. | sections | crit masked | unrestored | fabricated | forbidden | blocked |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for c, s in items[id_].items():
            L.append(f"| {c} | {s['correct']} | {s['score']} | {_f(s['entity_preservation'])} | {_f(s['factual_equivalence'])} | "
                     f"{_f(s['section_completeness'])} | {s['critical_masked']} | {s['unrestored']} | {s['fabricated']} | "
                     f"{s['forbidden_hits']} | {s['blocked']} |")
        L += [""]
    return "\n".join(L) + "\n"


def cmd_score(a) -> None:
    out = Path(a.out)
    tasks = load_jsonl(Path(a.tasks))
    finals = load_finals(out)
    if not finals:
        sys.exit("no final_*.jsonl files found")
    res = score_all(tasks, finals)
    for it in res["items"].values():
        for s in it.values():
            s.pop("fact_hits", None)
    (out / "scores.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "SCORES.md").write_text(render_scores_md(res), encoding="utf-8")
    prompts = {c: {r["id"]: r for r in load_jsonl(out / f"prompts_{c}.jsonl")} for c in finals
               if (out / f"prompts_{c}.jsonl").exists()}
    review = Path(a.review) if a.review else EUTIL / "REVIEW_CASES.md"
    review.write_text(render_review(tasks, finals, prompts, res), encoding="utf-8")
    print(f"scored conditions: {', '.join(finals)}; wrote {out / 'scores.json'}, {out / 'SCORES.md'}, {review}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "finish", "score"):
        p = sub.add_parser(name)
        p.add_argument("--tasks", default=str(EUTIL / "tasks.jsonl"))
        p.add_argument("--out", default=str(OUT))
        p.add_argument("--roster", default=str(EUTIL / "roster.jsonl"))
        if name != "score":
            p.add_argument("--condition", required=True, choices=CONDITIONS)
        else:
            p.add_argument("--review", default=None)
    a = ap.parse_args(argv)
    {"prepare": cmd_prepare, "finish": cmd_finish, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
