"""Tests for the Release-2 corpus generator (run: python -m pytest eval/synth2 -q)."""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import Counter

import pytest

from eval.synth import adversarial as r1_adv
from eval.synth import generate as r1_gen
from eval.synth2 import adversarial_heldout as adv
from eval.synth2 import generate as G
from eval.synth2.core import MIN_POS, MIN_SLICE, SLICES, TYPES, build_pools

SEED = G.DEFAULT_SEED
KEYS = ["id", "slice", "template_id", "name_source", "text", "spans"]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _toks(s):
    return {m.lower() for m in re.findall(r"[^\W\d_]+", s) if len(m) >= 3}


def _person_tokens(items):
    out = set()
    for it in items:
        for s in it["spans"]:
            if s["type"] == "PERSON":
                out |= _toks(it["text"][s["start"]:s["end"]])
    return out


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("data2")
    manifest = G.generate(str(out), SEED)
    d = {k: _read(os.path.join(out, f"{k}.jsonl")) for k in ("dev2", "test2", "ner_test2", "roster")}
    d["dir"], d["manifest"] = str(out), manifest
    return d


def test_determinism_same_seed_same_sha(corpus, tmp_path):
    G.generate(str(tmp_path), SEED)
    for name in ("roster.jsonl", "dev2.jsonl", "test2.jsonl", "ner_test2.jsonl", "MANIFEST.json"):
        assert _sha(os.path.join(corpus["dir"], name)) == _sha(os.path.join(tmp_path, name)), name


def test_different_seed_differs(corpus, tmp_path):
    G.generate(str(tmp_path), SEED + 1)
    assert _sha(os.path.join(tmp_path, "test2.jsonl")) != _sha(os.path.join(corpus["dir"], "test2.jsonl"))


def test_manifest_hashes_and_fields(corpus):
    m = corpus["manifest"]
    assert m["seed"] == SEED and m["generator_version"]
    for fn, h in m["files"].items():
        assert _sha(os.path.join(corpus["dir"], fn)) == h
    assert set(m["files"]) == {"roster.jsonl", "dev2.jsonl", "test2.jsonl", "ner_test2.jsonl"}
    for sp in ("dev2", "test2", "ner_test2"):
        assert m["splits"][sp]["positives_by_slice_type"]


@pytest.mark.parametrize("split", ["dev2", "test2", "ner_test2"])
def test_spans_wellformed(corpus, split):
    for it in corpus[split]:
        assert list(it) == KEYS
        text, prev = it["text"], 0
        for s in it["spans"]:
            assert s["type"] in TYPES
            assert 0 <= s["start"] < s["end"] <= len(text)
            assert s["start"] >= prev, "overlap or unsorted"
            v = text[s["start"]:s["end"]]
            assert v and v == v.strip(), repr(v)
            prev = s["end"]


def test_split_disjointness(corpus):
    dev, test = corpus["dev2"], corpus["test2"]
    assert {i["template_id"] for i in dev}.isdisjoint({i["template_id"] for i in test})
    td, tt, tn = _person_tokens(dev), _person_tokens(test), _person_tokens(corpus["ner_test2"])
    assert td and tt and tn
    assert not td & tt and not td & tn and not tt & tn


def test_roster_tokens_split_disjoint(corpus):
    ro = corpus["roster"]
    assert 1400 <= len(ro) <= 1600
    assert set(ro[0]) == {"employee_id", "full_name", "first_name", "last_name", "email", "phone"}
    assert len({r["employee_id"] for r in ro}) == len(ro)
    assert len({r["full_name"] for r in ro}) == len(ro)


def test_ner_names_absent_from_roster(corpus):
    ro = corpus["roster"]
    full = {r["full_name"].casefold() for r in ro}
    rtok = set()
    for r in ro:
        rtok |= _toks(r["full_name"]) | _toks(r["first_name"]) | _toks(r["last_name"])
    ner = corpus["ner_test2"]
    for it in ner:
        assert it["name_source"] == "external"
        for s in it["spans"]:
            if s["type"] == "PERSON":
                v = it["text"][s["start"]:s["end"]]
                assert v.casefold() not in full
                assert not _toks(v) & rtok, v


def test_ner_composition(corpus):
    ner = corpus["ner_test2"]
    assert len(ner) >= 400
    persons = [(it, s) for it in ner for s in it["spans"] if s["type"] == "PERSON"]
    assert len(persons) >= 1200
    assert len({it["template_id"] for it in ner}) >= 12
    hon = re.compile(r"(?:\b(?:dear|hi|hello|hey|mr|mrs|ms|dr|shri|smt|sri|regards|thanks)\b\.?\s*$)", re.I)
    plain = 0
    for it, s in persons:
        pre = it["text"][:s["start"]]
        line_pre = pre.rsplit("\n", 1)[-1]
        if not hon.search(line_pre) and line_pre.strip() != "":
            plain += 1
    assert plain / len(persons) >= 0.5, plain / len(persons)
    vals = [it["text"][s["start"]:s["end"]] for it, s in persons]
    assert any(re.fullmatch(r"[A-Z]\. [A-Z][a-z']+.*", v) for v in vals), "no initials"
    assert any(v == v.lower() and " " in v for v in vals), "no lowercase names"
    assert sum(1 for it in ner if "ne bola" in it["text"] or "hai" in it["text"]) > 10, "no Hinglish"
    other = Counter(s["type"] for it in ner for s in it["spans"] if s["type"] != "PERSON")
    assert len(other) >= 5


@pytest.mark.parametrize("split", ["dev2", "test2"])
def test_minimum_counts(corpus, split):
    items = corpus[split]
    cnt = Counter(s["type"] for it in items for s in it["spans"])
    for t in TYPES:
        assert cnt[t] >= MIN_POS[t], (split, t, cnt[t])
    assert cnt["PERSON"] >= 400 and cnt["COMPENSATION"] >= 200
    per_slice = Counter(it["slice"] for it in items)
    for sl in SLICES:
        assert per_slice[sl] >= MIN_SLICE[sl], (split, sl, per_slice[sl])
    assert all(not it["spans"] for it in items if it["slice"] == "negatives")
    assert all(it["spans"] for it in items if it["slice"] != "negatives")


def test_external_person_share(corpus):
    rtok = set()
    for r in corpus["roster"]:
        rtok |= _toks(r["full_name"])
    for split in ("dev2", "test2"):
        n = ext = 0
        for it in corpus[split]:
            for s in it["spans"]:
                if s["type"] == "PERSON":
                    n += 1
                    ext += not (_toks(it["text"][s["start"]:s["end"]]) & rtok)
        assert ext / n >= 0.30, (split, ext / n)


def test_no_unlabelled_name_tokens(corpus):
    """A pool name token appearing outside every span would be a hidden, unlabelled person mention."""
    pools = build_pools(SEED)
    tok = set()
    for p in pools.values():
        for d in p.values():
            for lst in d.values():
                for w in lst:
                    tok |= _toks(w)
    for split in ("dev2", "test2", "ner_test2"):
        for it in corpus[split]:
            t = it["text"]
            mask = bytearray(len(t))
            for s in it["spans"]:
                mask[s["start"]:s["end"]] = b"\x01" * (s["end"] - s["start"])
            for m in re.finditer(r"[^\W\d_]{3,}", t):
                assert not (m.group().lower() in tok and not any(mask[m.start():m.end()])), (it["id"], m.group())


def test_negative_lookalikes(corpus):
    neg = [it for sp in ("dev2", "test2") for it in corpus[sp] if it["slice"] == "negatives"]
    nums = [n for it in neg for n in re.findall(r"(?<!\d)\d{12}(?!\d)", it["text"])]
    valid = sum(r1_gen.verhoeff_valid(n) for n in nums)
    assert len(nums) > 200 and 0.04 <= valid / len(nums) <= 0.25, (valid, len(nums))
    blob = "\n".join(it["text"] for it in neg)
    for pat in (r"\b[0-9A-F]{2}:[0-9A-F]{2}:", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/", r"\b[A-Z]{3,4}-\d{3,4}\b", r"₹[\d,]+\.\d\d"):
        assert re.search(pat, blob, re.I), pat


def test_far_clue_documents(corpus):
    for sp in ("dev2", "test2"):
        far = [it for it in corpus[sp] if it["template_id"] in G.FAR_CLUE]
        assert len({it["template_id"] for it in far}) >= 2
        assert any(it["text"].count("\n") >= 20 for it in far)


def test_gitignore_covers_data():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
        assert "eval/data2/*.jsonl" in f.read().split("\n")


# ---------------------------------------------------------------- held-out adversarial transforms
def test_heldout_kinds_are_new():
    assert len(adv.KINDS) >= 3
    assert not set(adv.KINDS) & set(r1_adv.KINDS)
    assert "held out" in adv.__doc__.lower()


def test_adversarial_self_check():
    assert adv.self_check()


@pytest.mark.parametrize("kind", adv.KINDS)
def test_adversarial_kind_on_100_items(corpus, kind):
    pool = [it for it in corpus["test2"] if it["spans"] and adv.applicable(it, kind)]
    assert len(pool) >= 100, (kind, len(pool))
    rng = random.Random(f"{SEED}|{kind}")
    sample = rng.sample(pool, 100)
    edits = 0
    for it in sample:
        out = adv.transform(it, kind, rng)          # transform() runs check_item internally
        adv.check_item(it, out, kind)
        assert out["adv"]["kind"] == kind and len(out["spans"]) == len(it["spans"])
        edits += out["adv"]["edits"]
    assert edits >= 100
