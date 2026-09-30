"""Tests for the synthetic gold corpus generator (run: python -m pytest eval/synth -q)."""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import Counter

import pytest

from eval.synth import adversarial
from eval.synth import generate as G

SEED = 1337
ITEM_KEYS = ["id", "slice", "template_id", "name_source", "text", "spans"]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("gold")
    manifest = G.generate(str(out), SEED)
    data = {s: _read(os.path.join(out, f"{s}.jsonl")) for s in G.SPLITS}
    return {"dir": str(out), "manifest": manifest, "data": data, "roster": _read(os.path.join(out, "roster.jsonl"))}


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_determinism_same_seed_same_sha(corpus, tmp_path):
    G.generate(str(tmp_path), SEED)
    for name in ("roster.jsonl", "dev.jsonl", "tune.jsonl", "test.jsonl", "MANIFEST.json"):
        assert _sha(os.path.join(tmp_path, name)) == _sha(os.path.join(corpus["dir"], name)), name


def test_different_seed_differs(corpus, tmp_path):
    G.generate(str(tmp_path), SEED + 1)
    assert _sha(os.path.join(tmp_path, "test.jsonl")) != _sha(os.path.join(corpus["dir"], "test.jsonl"))


def test_manifest_hashes_match_files(corpus):
    m = corpus["manifest"]
    on_disk = json.load(open(os.path.join(corpus["dir"], "MANIFEST.json"), encoding="utf-8"))
    assert on_disk["seed"] == SEED and on_disk["generator_version"] == G.GENERATOR_VERSION
    for name, meta in on_disk["files"].items():
        assert _sha(os.path.join(corpus["dir"], name)) == meta["sha256"], name
    assert set(m["files"]) == {"roster.jsonl", "dev.jsonl", "tune.jsonl", "test.jsonl"}


def test_item_schema_and_spans(corpus):
    for split, items in corpus["data"].items():
        ids = set()
        for it in items:
            assert list(it) == ITEM_KEYS
            assert isinstance(it["text"], str) and it["text"]
            assert it["id"] not in ids
            ids.add(it["id"])
            assert it["id"].startswith(split + "-")
            assert it["slice"] in G.SLICES
            assert it["name_source"] in ("roster", "external", "mixed", "none")
            prev_end = 0
            for sp in it["spans"]:
                assert sp["type"] in G.TYPES
                assert 0 <= sp["start"] < sp["end"] <= len(it["text"]), it["id"]
                assert sp["start"] >= prev_end, f"overlap/unsorted in {it['id']}"
                prev_end = sp["end"]
                val = it["text"][sp["start"]:sp["end"]]
                assert val and val.strip() and val == val.strip(), (it["id"], repr(val))


def test_span_values_have_expected_shape(corpus):
    """Cheap sanity that gold values are what their label says (guards generator bugs)."""
    digits = lambda v: re.sub(r"\D", "", v)
    for items in corpus["data"].values():
        for it in items:
            for sp in it["spans"]:
                v, t = it["text"][sp["start"]:sp["end"]], sp["type"]
                if t == "EMAIL":
                    assert v.count("@") == 1 and "." in v.split("@")[1]
                elif t == "PHONE":
                    assert len(digits(v)) >= 10
                elif t == "AADHAAR":
                    assert len(digits(v)) == 12 and G.verhoeff_valid(digits(v)), v
                elif t == "PAN":
                    assert re.fullmatch(r"[A-Za-z]{3}[Pp][A-Za-z]\d{4}[A-Za-z]", v), v
                elif t == "CARD":
                    d = digits(v)
                    assert len(d) in (15, 16) and G.luhn_valid(d), v
                elif t == "IFSC":
                    assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v), v
                elif t == "BANK_ACCOUNT":
                    assert v.isdigit() and 9 <= len(v) <= 18, v
                elif t == "UPI_ID":
                    assert re.fullmatch(r"[\w.\-]+@[a-z]+", v), v
                elif t == "EMPLOYEE_ID":
                    assert re.fullmatch(r"EMP-\d{5}|E\d{6}|WD-\d{7}", v), v
                elif t == "DATE_OF_BIRTH":
                    assert re.search(r"(19[5-9]\d|20[0-2]\d)", v), v
                elif t == "COMPENSATION":
                    assert re.search(r"\d", v), v
                elif t == "SECRET":
                    assert len(v) >= 8 and " " not in v, v


def _tokens(text):
    return {m.group(0).lower() for m in re.finditer(r"[^\W\d_]+", text) if len(m.group(0)) > 1}


def test_split_disjoint_by_template(corpus):
    ts = {s: {it["template_id"] for it in items} for s, items in corpus["data"].items()}
    assert not (ts["dev"] & ts["tune"]) and not (ts["dev"] & ts["test"]) and not (ts["tune"] & ts["test"])
    for s in G.SPLITS:
        assert len(ts[s]) >= 8


def test_split_disjoint_by_name(corpus):
    toks = {}
    for s, items in corpus["data"].items():
        t = set()
        for it in items:
            for sp in it["spans"]:
                if sp["type"] == "PERSON":
                    t |= _tokens(it["text"][sp["start"]:sp["end"]])
        toks[s] = t
        assert len(t) > 50
    assert not (toks["dev"] & toks["tune"])
    assert not (toks["dev"] & toks["test"])
    assert not (toks["tune"] & toks["test"])


def test_name_source_mix(corpus):
    pm = corpus["manifest"]["person_mentions_by_name_source"]
    for s in G.SPLITS:
        total = sum(pm[s].values())
        assert 0.55 <= pm[s]["roster"] / total <= 0.80, (s, pm[s])
        assert pm[s]["external"] / total >= 0.25, (s, pm[s])   # plan: >= 30 % external names (approx.)


def test_roster(corpus):
    r = corpus["roster"]
    assert 1400 <= len(r) <= 1600
    assert len({x["employee_id"] for x in r}) == len(r)
    assert len({x["email"] for x in r}) == len(r)
    assert len({x["full_name"] for x in r}) == len(r)
    for x in r:
        assert set(x) == {"employee_id", "full_name", "first_name", "last_name", "email", "phone"}
        assert x["full_name"] == f"{x['first_name']} {x['last_name']}"
    # roster names of each split's items only come from the roster or the external pool, never both pools mixed
    roster_tokens = set()
    for x in r:
        roster_tokens |= _tokens(x["full_name"])
    ext = 0
    for it in corpus["data"]["test"]:
        if it["name_source"] == "external":
            for sp in it["spans"]:
                if sp["type"] == "PERSON":
                    ext += 1
                    assert not (_tokens(it["text"][sp["start"]:sp["end"]]) & roster_tokens)
    assert ext > 50


def test_minimum_counts(corpus):
    test_counts = Counter(sp["type"] for it in corpus["data"]["test"] for sp in it["spans"])
    for t, need in G.TEST_MIN.items():
        assert test_counts[t] >= need, (t, test_counts[t], need)
    assert dict(test_counts) == {k: v for k, v in corpus["manifest"]["span_counts"]["test"]["ALL"].items() if v}
    per_slice = Counter()
    for items in corpus["data"].values():
        for it in items:
            per_slice[it["slice"]] += 1
    assert per_slice["hr_free_text"] >= 1500
    assert per_slice["hris_structured"] >= 1000
    assert per_slice["integration_debug"] >= 500
    assert per_slice["negatives"] >= 1000
    for s, items in corpus["data"].items():
        assert {it["slice"] for it in items} == set(G.SLICES)
    ratio = {s: sum(len(v) for v in corpus["data"][s]) for s in G.SPLITS}
    total = sum(ratio.values())
    assert 0.5 <= ratio["dev"] / total <= 0.75


def test_negatives_have_no_spans_and_hard_ids(corpus):
    neg = [it for items in corpus["data"].values() for it in items if it["slice"] == "negatives"]
    assert sum(len(it["spans"]) for it in neg) == 0
    ids = [m for it in neg for m in re.findall(r"(?<!\d)\d{12}(?!\d)", it["text"])]
    assert len(ids) > 300
    valid = sum(G.verhoeff_valid(i) for i in ids)
    assert 0.03 < valid / len(ids) < 0.40      # most fail Verhoeff, some pass


def test_hard_cases_present(corpus):
    pers = [it["text"][sp["start"]:sp["end"]] for it in corpus["data"]["test"] for sp in it["spans"] if sp["type"] == "PERSON"]
    assert any(re.fullmatch(r"[A-Z]\. \S+", p) for p in pers)               # initials
    assert any(p == p.lower() and p.isalpha() for p in pers)                # lowercase
    assert any(" " not in p and p[:1].isupper() for p in pers)              # single token
    assert any(p.isupper() and len(p) > 3 for p in pers)                    # UPPER
    for s in G.SPLITS:
        c = Counter(sp["type"] for it in corpus["data"][s] for sp in it["spans"])
        assert all(c[t] > 0 for t in G.TYPES)


def test_verhoeff_known_vectors():
    assert G.verhoeff_check_digit("236") == "3" and G.verhoeff_valid("2363")
    assert not G.verhoeff_valid("2364")
    assert G.luhn_valid("4111111111111111") and not G.luhn_valid("4111111111111112")


def test_adversarial_transforms(corpus):
    assert adversarial.self_check()
    rng = random.Random(7)
    items = [it for it in corpus["data"]["test"] if it["spans"]]
    sample = rng.sample(items, 200)
    hits = Counter()
    for it in sample:
        for kind in adversarial.KINDS:
            out = adversarial.transform(it, kind, rng)       # transform() runs the span self-check
            assert out["id"].endswith("~" + kind) and len(out["spans"]) == len(it["spans"])
            for sp in out["spans"]:
                assert out["text"][sp["start"]:sp["end"]].strip()
            hits[kind] += out["adv"]["edits"] > 0
    assert all(hits[k] > 10 for k in adversarial.KINDS), hits
    with pytest.raises(ValueError):
        adversarial.transform(sample[0], "nope", rng)
