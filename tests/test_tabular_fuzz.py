"""Robustness fuzz: generated tabular, CSV, fixed-width, JSON and mixed HR documents through the whole pipeline.

Every document must be processed without an exception, and the final spans must be well formed: inside the text,
non-empty, never overlapping, and never crossing a line break (no entity type here is inherently multi-line: a postal
address written over several lines is protected line by line, which releases nothing). Synthetic values only; the
generator is seeded, so a failure names a reproducible document.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from pgw import egress
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.types import BlockedRequest, Context

POLICY = PolicyPack.load(Path(__file__).resolve().parents[1] / "policies/hris-default.yaml")
PROT = Protector(POLICY, Keys(bytes(range(32))))
CTX = Context("acme", "hr-assistant", "email_draft", conversation_id="fuzz-1")
N_DOCS = 300
MULTILINE_TYPES: frozenset = frozenset()     # decided: none (see module docstring)

_SYL = "ka ra ma na sha ta vi pri ya de su re la ni ga ji mo ha an ru pa ve ki du so".split()
_HEAD = ["EMP_ID", "EMPLOYEE NAME", "BASIC", "HRA", "NET_PAY", "DOB", "PAN", "MOBILE", "BANK A/C", "IFSC", "CTC",
         "Department", "Manager", "Location", "Remarks", "Joining Date", "Email", "Father's Name"]
_WORDS = ("salary arrears pending for October please check the payroll export attached Mother Father Regards Thanks "
          "Dear Hi team per month per year Rs INR revised gross net basic allowance bonus ticket ref invoice total "
          "Pune Mumbai Bengaluru Chennai the and of to with from by").split()


def _name(r: random.Random) -> str:
    w = lambda: "".join(r.choice(_SYL) for _ in range(r.randint(2, 3))).capitalize()  # noqa: E731
    n = f"{w()} {w()}"
    return n.upper() if r.random() < 0.4 else n


def _luhn(r: random.Random, n: int = 16) -> str:
    d = [r.choice("45")] + [str(r.randint(0, 9)) for _ in range(n - 2)]
    tot = 0
    for i, c in enumerate(reversed(d)):
        x = int(c) * (2 if i % 2 == 0 else 1)
        tot += x - 9 if x > 9 else x
    return "".join(d) + str((10 - tot % 10) % 10)


def _cell(r: random.Random, h: str) -> str:
    k = h.lower()
    if "name" in k or k == "manager":
        return _name(r)
    if k in ("basic", "hra", "net_pay", "ctc"):
        return r.choice([f"{r.randint(1000, 99999)}.{r.randint(0, 99):02d}", f"{r.randint(1, 99)},{r.randint(0, 99):02d},"
                         f"{r.randint(0, 999):03d}", str(r.randint(100, 999999)), f"Rs {r.randint(10, 9999)}"])
    if k == "emp_id":
        return r.choice([str(r.randint(10000, 99999)), f"EMP{r.randint(1000, 99999)}", f"KD-{r.randint(1000, 99999)}"])
    if k in ("dob", "joining date"):
        return f"{r.randint(1, 28):02d}/{r.randint(1, 12):02d}/{r.randint(1960, 2005)}"
    if k == "pan":
        return "".join(r.choice("ABCDEFGHJK") for _ in range(5)) + f"{r.randint(1000, 9999)}" + r.choice("ABCDEF")
    if k == "mobile":
        return r.choice(["9", "8", "7"]) + "".join(str(r.randint(0, 9)) for _ in range(9))
    if k == "bank a/c":
        return r.choice([str(r.randint(10**10, 10**13)), _luhn(r), " ".join(_luhn(r)[i:i + 4] for i in (0, 4, 8, 12))])
    if k == "ifsc":
        return "".join(r.choice("ABCDHIKSUY") for _ in range(4)) + "0" + f"{r.randint(0, 999999):06d}"
    if k == "email":
        return f"{_name(r).split()[0].lower()}.{r.randint(1, 99)}@example.com"
    return " ".join(r.choice(_WORDS) for _ in range(r.randint(0, 3)))


def _table(r: random.Random, kind: str) -> str:
    heads = r.sample(_HEAD, r.randint(3, 7))
    rows = [[_cell(r, h) for h in heads] for _ in range(r.randint(1, 6))]
    if kind == "csv":
        sep = r.choice([",", "\t", ";", "|"])
        return "\n".join(sep.join(x) for x in ([heads] if r.random() < 0.8 else []) + rows)
    if kind == "fixed":
        w = [max(len(x[i]) for x in [heads] + rows) + r.randint(1, 3) for i in range(len(heads))]
        gap = " " if r.random() < 0.3 else ""
        lines = ([heads] if r.random() < 0.8 else []) + rows
        return "\n".join(gap.join(c.ljust(w[i]) if gap == "" else c for i, c in enumerate(x)).rstrip() for x in lines)
    return json.dumps([dict(zip(heads, x, strict=True)) for x in rows], indent=r.choice([None, 2]))


def _prose(r: random.Random) -> str:
    out = []
    for _ in range(r.randint(1, 4)):
        bits = [r.choice(_WORDS) for _ in range(r.randint(3, 12))]
        if r.random() < 0.5:
            bits.insert(r.randint(0, len(bits)), r.choice(["Mr.", "Dear", "with", "Regards,\n"]) + " " + _name(r))
        if r.random() < 0.4:
            bits.insert(r.randint(0, len(bits)), f"Rs {r.randint(10, 99999)} per {r.choice(['month', 'year'])}")
        out.append(" ".join(bits))
    return r.choice(["\n", ". ", ".\n\n"]).join(out)


def documents(n: int = N_DOCS, seed: int = 20261028) -> list[str]:
    r = random.Random(seed)
    docs = []
    for i in range(n):
        kind = ("csv", "fixed", "json", "mixed")[i % 4]
        if kind == "mixed":
            parts = [_prose(r), _table(r, r.choice(["csv", "fixed", "json"])), _prose(r)]
            docs.append(r.choice(["\n", "\n\n", "\r\n"]).join(parts[: r.randint(2, 3)]))
        else:
            docs.append(_table(r, kind))
    return docs


DOCS = documents()


def _assert_well_formed(text: str, dets) -> None:
    prev = None
    for d in sorted(dets, key=lambda d: d.start):
        assert 0 <= d.start < d.end <= len(text), (d, text)
        if prev is not None:
            assert prev.end <= d.start, ("overlap", prev, d, text)
        if d.type not in MULTILINE_TYPES:
            assert "\n" not in text[d.start:d.end], ("crosses a line break", d, text[d.start:d.end], text)
        prev = d


@pytest.mark.parametrize("chunk", range(10))
def test_generated_documents_are_processed_and_spans_are_well_formed(chunk):
    for text in DOCS[chunk::10]:
        raw = PROT.detect_text(text)                     # every view's hits (they may overlap; resolution follows)
        for d in raw:
            assert 0 <= d.start < d.end <= len(text)
            if d.type.value == "PERSON":
                assert "\n" not in text[d.start:d.end], (d, text)
        msgs = [{"role": "user", "content": text}]
        (final,) = PROT.analyze(msgs, CTX)
        _assert_well_formed(text, final)
        try:
            prot = PROT.protect(msgs, CTX)
        except BlockedRequest:
            continue                                     # a deliberate refusal (policy BLOCK), not a crash
        assert isinstance(prot.messages[0]["content"], str)
        # what the gateway would send passes its own egress invariant (a hit there is a 500 for a routine document)
        egress.check(json.dumps({"model": "m", "messages": prot.messages}).encode(), prot.sensitive_values)


def test_the_generator_covers_every_shape():
    assert len(DOCS) == N_DOCS
    assert sum(t.lstrip().startswith(("[", "{")) for t in DOCS) >= 50                 # JSON
    assert sum("  " in t and "," not in t.split("\n")[0] for t in DOCS) >= 50         # fixed-width
