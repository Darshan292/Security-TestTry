"""Faker en_IN as an ADDITIONAL, flagged, seeded source of person names / addresses (ml/train/values.py), and the
documented finding that Faker must NOT be used for identifiers. Skipped when faker is not installed."""
import random
import re

import pytest

pytest.importorskip("faker")
from faker import Faker  # noqa: E402
from ml.train import structured, synth  # noqa: E402
from ml.train import values as V  # noqa: E402


@pytest.fixture(autouse=True)
def _faker_off():
    V.disable_faker()
    yield
    V.disable_faker()


def test_default_is_off_and_does_not_change_existing_seeds():
    before = [V.person(random.Random(i)) for i in range(50)]
    V.enable_faker(0.0)
    assert before == [V.person(random.Random(i)) for i in range(50)]
    assert all(p.region != "faker_en_in" for p in before)


def test_enabled_draws_a_share_from_faker_and_is_seeded():
    V.enable_faker(0.5)
    a = [V.person(random.Random(i)) for i in range(300)]
    b = [V.person(random.Random(i)) for i in range(300)]
    assert a == b                                              # same seed, same output (Faker is re-seeded from rng)
    share = sum(p.region == "faker_en_in" for p in a) / len(a)
    assert 0.35 < share < 0.65
    for p in (x for x in a if x.region == "faker_en_in"):
        assert re.fullmatch(r"[A-Za-z][A-Za-z'-]{1,24}", p.first) and re.fullmatch(r"[A-Za-z][A-Za-z'-]{1,24}", p.last)


def test_region_argument_bypasses_faker_and_share_one_is_all_faker():
    V.enable_faker(1.0)
    assert V.person(random.Random(1), "bengali").region == "bengali"
    assert all(V.person(random.Random(i)).region == "faker_en_in" for i in range(40))
    with pytest.raises(ValueError):
        V.enable_faker(1.5)


def test_faker_names_add_diversity():
    base = {V.person(random.Random(i)).first for i in range(400)}
    V.enable_faker(1.0)
    extra = {V.person(random.Random(i)).first for i in range(400)}
    assert len(extra - base) > 100


def test_address_block_uses_faker_addresses_when_enabled():
    V.enable_faker(1.0)
    text, spans = structured.address_block(random.Random(5))
    assert re.search(r" - \d{6}\n", text)
    assert {s["type"] for s in spans} >= {"PERSON", "PHONE"}
    for s in spans:
        assert 0 <= s["start"] < s["end"] <= len(text)


def test_programmatic_records_still_valid_with_faker_on():
    V.enable_faker(0.4)
    recs = synth.programmatic_records(40, seed=3)
    assert len(recs) == 40
    for r in recs:
        for sp in r["spans"]:
            assert r["text"][sp["start"]:sp["end"]].strip()


# ------------------------------------------------------------------------ findings: identifiers stay with the repo
def _fk(seed):
    f = Faker("en_IN")
    f.seed_instance(seed)
    return f


def test_faker_aadhaar_is_luhn_not_verhoeff():
    """Faker's en_IN aadhaar_id satisfies the Luhn checksum (every sample) but not Verhoeff, the real Aadhaar checksum:
    on 2000 samples about 9-11% are valid Aadhaars, by chance (1 in 10), and ~12% start with 0 or 1, which are not
    issued. The repo's generator is Verhoeff-valid for 100% and never starts with 0/1."""
    f = _fk(1)
    sample = [f.aadhaar_id() for _ in range(2000)]
    luhn = sum(V.luhn_valid(x) for x in sample) / len(sample)
    verhoeff = sum(V.verhoeff_valid(x) for x in sample) / len(sample)
    lead01 = sum(x[0] in "01" for x in sample) / len(sample)
    assert luhn == 1.0
    assert 0.05 < verhoeff < 0.15
    assert 0.08 < lead01 < 0.16
    mine = [re.sub(r"\D", "", V.aadhaar(random.Random(i), "plain")) for i in range(500)]
    assert all(V.verhoeff_valid(x) and x[0] not in "01" for x in mine)
    stdnum = pytest.importorskip("stdnum.in_.aadhaar")
    assert sum(stdnum.is_valid(x) for x in sample) / len(sample) < 0.15


def test_faker_pan_and_gstin_against_stdnum_or_repo():
    """Faker's PAN is structurally valid (stdnum: 99.9%); its GSTIN has a correct checksum character, and the few
    stdnum rejections are state code 38 (Ladakh), which the installed stdnum table lacks."""
    f = _fk(2)
    pans = [f.pan() for _ in range(1000)]
    gstins = [f.gstin() for _ in range(1000)]
    from pgw.detect.idshape import _gstin_char
    assert all(re.fullmatch(r"[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]", p) for p in pans)
    assert all(g[14] == _gstin_char(g[:14]) and re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g) for g in gstins)
    pan_mod = pytest.importorskip("stdnum.in_.pan")
    gstin_mod = pytest.importorskip("stdnum.in_.gstin")
    assert sum(pan_mod.is_valid(p) for p in pans) / len(pans) > 0.99
    bad = [g for g in gstins if not gstin_mod.is_valid(g)]
    assert len(bad) / len(gstins) < 0.06
    assert all(g.startswith("38") or g[:2] not in {"%02d" % i for i in range(1, 38)} for g in bad)
