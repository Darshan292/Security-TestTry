"""R2 regressions for the streaming defects found by the one-shot R1 TEST (W4, W5)."""
import random

from pgw.alias import ALIAS_RE
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.stream import GATE_CONTEXT_MAX, Restorer
from pgw.types import Context

POLICY = PolicyPack.load("policies/hris-default.yaml")
PROT = Protector(POLICY, Keys(bytes(range(32))))
CTX = Context("acme", "app", "eval_tokenize_all", conversation_id="c1")


def run(r: Restorer, text: str, cuts) -> str:
    out, prev = [], 0
    for c in sorted(set(cuts)) + [len(text)]:
        out.append(r.feed(text[prev:c]))
        prev = c
    out.append(r.finish())
    return "".join(out)


def buffered(amap, text):
    return run(Restorer(amap, mode="buffered"), text, [])


def assert_stream_equals_buffered(amap, text, trials=200, seed=0):
    want = buffered(amap, text)
    rng = random.Random(seed)
    for _ in range(trials):
        cuts = rng.sample(range(1, len(text)), k=min(len(text) - 1, rng.randint(1, 60)))
        got = run(Restorer(amap, mode="incremental"), text, cuts)
        assert got == want
    return want


def test_w5_minified_json_longer_than_token_max_never_splits_aliases():
    rows = ",".join('{"firstName":"Aishwarya Raju","email":"aishwarya.raju%d@acme.in","empId":"EMP-%05d"}' % (i, i)
                    for i in range(12))
    p = PROT.protect([{"role": "user", "content": "[" + rows + "]"}], CTX)
    aliased = p.messages[0]["content"]
    assert " " not in aliased.replace("Aishwarya Raju", "") and len(aliased) > 1000   # one long run
    out = assert_stream_equals_buffered(p.alias_map, aliased)
    assert not ALIAS_RE.search(out)                  # every alias restored
    assert out == "[" + rows + "]"
    for i in range(1, len(aliased)):                 # and at every single cut offset
        assert run(Restorer(p.alias_map, mode="incremental"), aliased, [i]) == out


def test_w4_context_dependent_raw_pii_masked_in_stream_as_in_buffer():
    header = "Emp ID\tName\tAadhaar\tCity\n"
    filler = "".join(f"E{100000 + i}\tPerson {i}\tpending\tPune\n" for i in range(30))
    raw_row = "E999999\tUsha Chettiar\t776822697316\tKolkata\n"
    model_output = header + filler + raw_row + "Summary: one record has an unmasked id.\n"
    want = buffered({}, model_output)
    assert "776822697316" not in want                # buffered gate masks it (TSV column context)
    got = assert_stream_equals_buffered({}, model_output, trials=100)
    assert "776822697316" not in got


def test_degrades_to_buffered_past_gate_context_cap():
    text = ("word " * (GATE_CONTEXT_MAX // 5 + 100)) + "Aadhaar 2341 2341 2346 end"
    r = Restorer({}, mode="incremental")
    first = r.feed(text[: GATE_CONTEXT_MAX // 2])
    rest = r.feed(text[GATE_CONTEXT_MAX // 2:]) + r.finish()
    assert "2341 2341 2346" not in first + rest


def test_policy_buffers_restricted_purposes_and_unknown_purposes():
    for purpose in ("hr_case_summary", "email_draft", "integration_debug", "comp_band_analysis", "no_such_purpose"):
        assert POLICY.stream_mode(purpose) == "buffered", purpose
    assert POLICY.stream_mode("eval_tokenize_all") == "incremental"


def test_buffered_mode_releases_nothing_until_finish():
    r = Restorer({}, mode="buffered")
    assert r.feed("hello " * 100) == ""
    assert r.finish() == "hello " * 100


def test_multi_token_roster_name_not_partially_released():
    """R2 dev2: 'bhavesh ' was released before 'chauhan' arrived; buffered masked the whole name."""
    from pgw.detect.edm import RosterIndex
    roster = RosterIndex(b"E" * 32)
    roster.add_employee({"full_name": "Bhavesh Chauhan", "first_name": "Bhavesh", "last_name": "Chauhan",
                         "email": "b@acme.in", "employee_id": "EMP-1", "phone": ""})
    text = '{"id": "x", "username": "bhavesh chauhan", "extra": {"k": "some more trailing text here"}}\n'
    want = run(Restorer({}, roster=roster, mode="buffered"), text, [])
    assert "bhavesh" not in want
    rng = random.Random(1)
    for _ in range(300):
        cuts = rng.sample(range(1, len(text)), k=rng.randint(1, 40))
        assert run(Restorer({}, roster=roster, mode="incremental"), text, cuts) == want
