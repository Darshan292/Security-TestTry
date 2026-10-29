"""Compare the built-in validators with the python-stdnum and phonenumbers adapters (docs/PLUGINS.md).

Inputs, DEV material only:
  1. the DEV corpora eval/data/dev.jsonl, eval/data/tune.jsonl, eval/data3/dev3build.jsonl (gunzip the .gz first if
     the .jsonl is absent): every shape candidate (pgw.plugins.candidates) is validated by all three, and the gold
     span label of the corpus says whether the candidate really is that type;
  2. the synthetic generators of ml/train/values.py (checksum-valid values) and invalid mutants of them;
  3. spec edge vectors (published test numbers and the rules each spec states), listed in EDGE below.

Never reads anything named *heldout*, test*, sealed or tester sets, and never dev3val (it has its own run budget,
CLAUDE.md): _guard() refuses such a path before it is opened.

Prints, per entity type, a matrix of verdict triples (builtin, stdnum, phonenumbers; '-' = UNKNOWN / not covered)
with counts, and for each disagreement class up to three SYNTHETIC examples (corpus values are never printed).

    python scripts/compare_validators.py [--n 2000] [--seed 7]
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.train import values as V  # noqa: E402
from pgw.plugins.adapters import BuiltinValidator, PhonenumbersValidator, StdnumValidator  # noqa: E402
from pgw.plugins.candidates import candidates  # noqa: E402

CORPORA = ("eval/data/dev.jsonl", "eval/data/tune.jsonl", "eval/data3/dev3build.jsonl")
_FORBIDDEN = re.compile(r"heldout|test|sealed|tester|dev3val|v3-sealed", re.I)
GOLD = {"AADHAAR": {"AADHAAR", "AADHAAR_LIKE"}, "PAN": {"PAN"}, "IFSC": {"IFSC"}, "CARD": {"CARD"},
        "UPI_ID": {"UPI_ID"}, "PHONE": {"PHONE"}, "IBAN": {"BANK_ACCOUNT"}}


def _guard(path: Path) -> Path:
    if _FORBIDDEN.search(str(path.relative_to(ROOT))):
        raise SystemExit(f"refusing to read {path.name}: held-out / test / tester material is never read here")
    return path


def _lines(rel: str):
    p = _guard(ROOT / rel)
    if p.is_file():
        yield from p.read_text(encoding="utf-8").splitlines()
    elif p.with_suffix(".jsonl.gz").is_file():
        with gzip.open(_guard(p.with_suffix(".jsonl.gz")), "rt", encoding="utf-8") as f:
            yield from f


# --- synthetic values and spec edge vectors -------------------------------------------------------------------------

def _mutate_digit(s: str, rng: random.Random) -> str:
    idx = [i for i, c in enumerate(s) if c.isdigit()]
    i = idx[-1] if rng.random() < 0.5 else rng.choice(idx)
    return s[:i] + str((int(s[i]) + rng.randint(1, 9)) % 10) + s[i + 1:]


def _verhoeff_palindromes(limit: int) -> list[str]:
    out = []
    for half in range(200000, 1000000):
        h = str(half)
        n = h + h[::-1]
        if V.verhoeff_valid(n):
            out.append(n)
            if len(out) >= limit:
                break
    return out


def _iban(rng: random.Random, cc: str, n: int) -> str:
    from pgw.detect import recognizers as R
    bban = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(n - 4))
    for k in range(100):
        cand = f"{cc}{k:02d}{bban}"
        if R.iban_valid(cand):
            return cand
    raise AssertionError


EDGE = {
    # Aadhaar: UIDAI numbering scheme (12 digits, first digit 2-9, Verhoeff check digit); stdnum adds "not a
    # palindrome". 234123412346 is the example of the stdnum docs.
    "AADHAAR": ["234123412346", "234123412347", "123412341234", "2341 2341 2346", "0000 0000 0000"],
    # PAN: CBDT structure AAAAA9999A, 4th character = holder type (P C H F A T B L J G per the Income Tax
    # Department; stdnum also accepts K, "Krish (Trust Krish)"), sequential number 0001-9999.
    "PAN": ["ACUPA7085R", "ABMXA3211G", "ACUPA0000R", "AAAKA1234A", "acupa7085r", "ABCPE 1234F"],
    # Card: ISO/IEC 7812 (Luhn) with the networks' published test numbers.
    "CARD": ["4111111111111111", "5555555555554444", "378282246310005", "6011111111111117", "3530111333300000",
             "6200000000000005", "30569309025904", "6445644564456445", "6759649826438453", "2223003122003222",
             "4111111111111112", "1234567812345670"],
    "GSTIN": ["27AAPFU0939F1ZV", "27AAPFU0939F1Z", "27AAPFU0939F1AA", "27AAPFU0939F1ZO"],
    "EPIC": ["WKH1186253", "WKH118624", "WKH1186263"],
    "VID": ["2341234123412341", "2341234123412342", "1341234123412341"],
    "IBAN": ["GB82 WEST 1234 5698 7654 32", "DE89 3704 0044 0532 0130 00", "DE89 3704 0044 0532 0130 01",
             "IN12 3456 7890 1234 5678"],
    "PHONE": ["+91 98765 43210", "+91 12345 67890", "022-2345 6789", "+1 650 253 0000", "5876543210",
              "+91 60000 00000", "080-12345678", "1800-123-4567"],
}


def synthetic(n: int, seed: int) -> dict[str, list[tuple[str, str]]]:
    """{type: [(value, origin)]}; origin is 'gen' (generator: checksum-valid) or 'mut' (a mutant) or 'edge'."""
    rng = random.Random(seed)  # noqa: S311 - reproducible synthetic data, not crypto
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for _ in range(n):
        a = V.aadhaar(rng)
        out["AADHAAR"] += [(a, "gen"), (_mutate_digit(a, rng), "mut")]
        p = V.pan(rng, holder=rng.choice("PCHFATBLJGK"))
        out["PAN"] += [(p, "gen")]
        c = V.card(rng)
        out["CARD"] += [(c, "gen"), (_mutate_digit(c, rng), "mut")]
        out["IFSC"] += [(V.ifsc(rng), "gen")]
        ph = V.phone(rng)
        out["PHONE"] += [(ph, "gen")]
        out["UPI_ID"] += [(V.upi_id(rng, V.person(rng)), "gen")]
        g14 = f"{rng.randint(1, 37):02d}" + V.pan(rng, holder=rng.choice("PCF")) + rng.choice("123456789") + "Z"
        from pgw.detect.idshape import _gstin_char   # the repo's own GSTIN check-character generator
        out["GSTIN"] += [(g14 + _gstin_char(g14), "gen")]
        vid = rng.choice("23456789") + "".join(rng.choice("0123456789") for _ in range(14))
        out["VID"] += [(vid + V.verhoeff_check_digit(vid), "gen")]
    from pgw.detect import recognizers as R
    countries = sorted(R._IBAN_LEN.items())
    for _ in range(max(50, n // 10)):
        cc, ln = rng.choice(countries)
        out["IBAN"] += [(_iban(rng, cc, ln), "gen")]
    out["AADHAAR"] += [(x, "edge") for x in _verhoeff_palindromes(20)]
    for t, vals in EDGE.items():
        out[t] += [(v, "edge") for v in vals]
    return out


# --- comparison ------------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    b, s, ph = BuiltinValidator(), StdnumValidator(), PhonenumbersValidator()

    def triple(t, v):
        return tuple("-" if x.value == "UNKNOWN" else x.value[0] for x in (b.validate(t, v), s.validate(t, v),
                                                                           ph.validate(t, v)))
    print("verdicts: (builtin, stdnum, phonenumbers); V valid, I invalid, - no opinion\n")
    print("=== synthetic generators, mutants and spec edge vectors ===")
    for t, items in sorted(synthetic(a.n, a.seed).items()):
        m: Counter = Counter()
        ex: dict = defaultdict(list)
        for v, origin in items:
            k = (origin, triple(t, v))
            m[k] += 1
            if len(ex[k]) < 3:
                ex[k].append(v)
        print(f"-- {t}")
        for (origin, tr), c in sorted(m.items()):
            known = [x for x in tr if x != "-"]
            flag = "  DISAGREE" if len(set(known)) > 1 else ""
            print(f"   {origin:4s} {''.join(tr)} {c:6d}{flag}   e.g. {', '.join(ex[(origin, tr)])}")

    print("\n=== DEV corpora: shape candidates (gold = the corpus labels the candidate as that type) ===")
    m = Counter()
    for rel in CORPORA:
        for line in _lines(rel):
            if not line.strip():
                continue
            rec = json.loads(line)
            text, spans = rec["text"], rec.get("spans", [])
            for t, i, j in candidates(text, None)[0]:
                cand = text[i:j]
                gold = any(sp["type"] in GOLD.get(t, ()) and sp["start"] < j and i < sp["end"] for sp in spans)
                m[(t, "gold" if gold else "not-gold", triple(t, cand))] += 1
    for (t, g, tr), c in sorted(m.items()):
        known = [x for x in tr if x != "-"]
        flag = "  DISAGREE" if len(set(known)) > 1 else ""
        print(f"   {t:8s} {g:8s} {''.join(tr)} {c:6d}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
