"""Release-2 corpus generator (v2): dev2 / test2 / ner_test2 + new roster.

Authored without reference to the detector code or R2 fixes (EVALUATION_PLAN.md, Amendment 2026-09-30b, items 1-2).
Every gold span is computed by construction (Builder appends values and records offsets; nothing is located by search).

    python -m eval.synth2.generate --out eval/data2 [--seed 20260930]

Stdlib only; deterministic for (seed, GENERATOR_VERSION).  Splits are disjoint by template_id and by person-name token.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

from . import t_hr, t_hris, t_integ, t_neg, t_ner  # noqa: F401  (register templates)
from .core import (DEFAULT_SEED, GENERATOR_VERSION, MIN_POS, MIN_SLICE, NER_TEMPLATES, SLICES, SPLITS, TEMPLATES, TYPES, G,
                   build_pools, build_roster, roster_rows)

SLICE_ABBR = {"hr_free_text": "hr", "hris_structured": "hris", "integration_debug": "dbg", "negatives": "neg"}
NER_MIN_ITEMS, NER_MIN_PERSON = 420, 1250
# documents whose contextual clue is far from the value (header 20+ rows up, label at top of a long form, value first / explained later)
FAR_CLUE = {"long_form_top_label", "legend_then_rows", "number_first_explained_later", "json_columnar_far_header",
            "csv_long_export_far_header", "long_config_secret_at_bottom"}
OTHER_MIN = {"PERSON": 1250}


def _tokens(s):
    return {m.lower() for m in re.findall(r"[^\W\d_]+", s) if len(m) >= 3}


def probe_types(pools, roster, ext_pool, fn, ner=False):
    """types a template can emit (union over a few throw-away runs)."""
    seen = set()
    for k in range(10):
        g = G(random.Random(f"probe|{fn.tid}|{k}"), "dev2", roster, ext_pool, {"n": 0, "ext": 0}, ner)
        fn(g)
        _, spans = g.b.finish()
        seen |= {s["type"] for s in spans}
    return seen


def assign_templates(seed, pools, people):
    """template_id -> split, alternating within each slice; retry until every type is emitted by >= 2 templates per split."""
    info = {}
    for sl in SLICES:
        for tid, fn in TEMPLATES[sl].items():
            info[tid] = probe_types(pools, people["dev2"], pools["ext_dev2"], fn)
    for attempt in range(500):
        r = random.Random(f"{seed}|assign|{attempt}")
        assign = {}
        for sl in SLICES:
            tids = sorted(TEMPLATES[sl])
            r.shuffle(tids)
            k = (len(tids) + r.randint(0, 1)) // 2
            for i, t in enumerate(tids):
                assign[t] = SPLITS[0] if i < k else SPLITS[1]
        ok = all(sum(1 for t, s in assign.items() if s == sp and ty in info[t]) >= 3 for sp in SPLITS for ty in TYPES)
        ok = ok and all(sum(1 for t, s in assign.items() if s == sp and TEMPLATES[sl].get(t)) >= 8 for sp in SPLITS for sl in SLICES)
        ok = ok and all(sum(1 for t, s in assign.items() if s == sp and t in FAR_CLUE) >= 2 for sp in SPLITS)
        if ok:
            return assign, info
    raise RuntimeError("could not find a template split with full type coverage")


def make_item(seed, split, fn, k, roster, ext_pool, state, item_id, ner=False):
    g = G(random.Random(f"{seed}|{split}|{fn.tid}|{k}"), split, roster, ext_pool, state, ner)
    fn(g)
    text, spans = g.b.finish()
    srcs = set(g.b.srcs)
    ns = "none" if not srcs else next(iter(srcs)) if len(srcs) == 1 else "mixed"
    for s in spans:
        v = text[s["start"]:s["end"]]
        assert v and v == v.strip(), f"span with edge whitespace in {fn.tid}: {v!r}"
    return {"id": item_id, "slice": fn.slice, "template_id": fn.tid, "name_source": ns, "text": text, "spans": spans}


def count_types(items):
    c = Counter()
    for it in items:
        for s in it["spans"]:
            c[s["type"]] += 1
    return c


def gen_split(seed, split, tids, info, people, pools):
    roster, ext_pool = people[split], pools[f"ext_{split}"]
    state = {"n": 0, "ext": 0}
    items, counter = [], 0
    by_slice = defaultdict(list)
    for sl in SLICES:
        tl = sorted(t for t in tids if t in TEMPLATES[sl])
        rr = random.Random(f"{seed}|{split}|{sl}|order")
        order = []
        n = MIN_SLICE[sl] + 20
        while len(order) < n:
            cyc = list(tl)
            rr.shuffle(cyc)
            order += cyc
        for tid in order[:n]:
            counter += 1
            it = make_item(seed, split, TEMPLATES[sl][tid], counter, roster, ext_pool, state, f"{split}-{SLICE_ABBR[sl]}-{counter:05d}")
            items.append(it)
    # top-up to statistical-power minima
    cnt = count_types(items)
    rr = random.Random(f"{seed}|{split}|topup")
    guard = 0
    while True:
        deficit = [t for t in TYPES if cnt[t] < MIN_POS[t] + 10]
        if not deficit:
            break
        guard += 1
        assert guard < 20000, f"top-up failed for {deficit}"
        t = deficit[0]
        cands = sorted(x for x in tids if t in info[x] and TEMPLATES_BY_ID[x].slice != "negatives")
        tid = rr.choice(cands)
        counter += 1
        it = make_item(seed, split, TEMPLATES_BY_ID[tid], counter, roster, ext_pool, state, f"{split}-{SLICE_ABBR[TEMPLATES_BY_ID[tid].slice]}-{counter:05d}")
        items.append(it)
        for s in it["spans"]:
            cnt[s["type"]] += 1
    items.sort(key=lambda x: x["id"])
    return items, state


TEMPLATES_BY_ID = {}


def gen_ner(seed, pools):
    tl = sorted(NER_TEMPLATES)
    state = {"n": 0, "ext": 0}
    items, cnt = [], Counter()
    rr = random.Random(f"{seed}|ner|order")
    k = 0
    while len(items) < NER_MIN_ITEMS or cnt["PERSON"] < NER_MIN_PERSON:
        cyc = list(tl)
        rr.shuffle(cyc)
        for tid in cyc:
            k += 1
            it = make_item(seed, "ner", NER_TEMPLATES[tid], k, [], pools["ner"], state, f"ner-{k:05d}", ner=True)
            items.append(it)
            for s in it["spans"]:
                cnt[s["type"]] += 1
    return items


def _dump(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def generate(out_dir, seed=DEFAULT_SEED):
    os.makedirs(out_dir, exist_ok=True)
    for sl in SLICES:
        TEMPLATES_BY_ID.update(TEMPLATES[sl])
    pools = build_pools(seed)
    people = build_roster(seed, pools)
    assign, info = assign_templates(seed, pools, people)
    _dump(os.path.join(out_dir, "roster.jsonl"), roster_rows(people))
    manifest = {"generator": "eval.synth2.generate", "generator_version": GENERATOR_VERSION, "seed": seed, "splits": {}, "files": {}}
    for sp in SPLITS:
        tids = sorted(t for t, s in assign.items() if s == sp)
        items, state = gen_split(seed, sp, tids, info, people, pools)
        _dump(os.path.join(out_dir, f"{sp}.jsonl"), items)
        per = {sl: Counter() for sl in SLICES}
        n_items = Counter(it["slice"] for it in items)
        rtok = set()
        for pp in people[sp]:
            rtok |= _tokens(pp.full)
        n_p = n_ext = 0
        for it in items:
            for s in it["spans"]:
                per[it["slice"]][s["type"]] += 1
                if s["type"] == "PERSON":
                    n_p += 1
                    n_ext += not (_tokens(it["text"][s["start"]:s["end"]]) & rtok)
        manifest["splits"][sp] = {
            "items": dict(n_items), "templates": {sl: sorted(t for t in tids if t in TEMPLATES[sl]) for sl in SLICES},
            "positives_by_slice_type": {sl: {t: per[sl][t] for t in TYPES if per[sl][t]} for sl in SLICES},
            "positives_total": {t: sum(per[sl][t] for sl in SLICES) for t in TYPES},
            "person_mentions_external_share": round(n_ext / max(1, n_p), 4)}
    ner = gen_ner(seed, pools)
    _dump(os.path.join(out_dir, "ner_test2.jsonl"), ner)
    nc = count_types(ner)
    manifest["splits"]["ner_test2"] = {"items": {"ner_free_text": len(ner)}, "templates": {"ner_free_text": sorted(NER_TEMPLATES)},
                                       "positives_by_slice_type": {"ner_free_text": {t: nc[t] for t in TYPES if nc[t]}},
                                       "positives_total": {t: nc[t] for t in TYPES if nc[t]}}
    for fn in ("roster.jsonl", "dev2.jsonl", "test2.jsonl", "ner_test2.jsonl"):
        manifest["files"][fn] = _sha(os.path.join(out_dir, fn))
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="eval/data2")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = ap.parse_args(argv)
    m = generate(a.out, a.seed)
    for sp, d in m["splits"].items():
        print(sp, d["items"], d["positives_total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
