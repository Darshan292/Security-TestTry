"""Address coverage is never silent (pgw.gateway.coverage): a production gateway serving a contract with free text
(`requires_detectors: [ADDRESS]`, default for EXT_PSEUDO/LOCAL_COMPUTE) starts only with an ML detector label mapped to
ADDRESS or with the sealed opt-out `address_coverage: pin_only`; /healthz and every pgw_report state which one is in
force. Without ML, a masked PIN also masks the structural address block it ends (`pin_block`), measured here on
generated home addresses and on benign text. Synthetic data only."""
import hashlib
import os

import pytest
import yaml
from starlette.testclient import TestClient

from pgw.detect import places
from pgw.detect.recognizers import detect_d2
from pgw.gateway.app import create_app
from pgw.gateway.coverage import CoverageError
from pgw.gateway.settings import ProfileError, Settings
from pgw.gateway.upstream import MockLLMUpstream
from pgw.product.contracts import ContractError, load_contract
from tests.address_cases import BENIGN, home_addresses
from tests.sealed import ROOT, attest, production

KEY = "coverage-key"
STREET = "Near Hanuman Temple, Gandhi Chowk, Koramangala 4th Block, Bengaluru - 560034"


def gateway(tmp, deployment=None, contract=None, mode="production", transparency=True):
    contract = contract or ROOT / "contracts/ticket_summary.yaml"
    mock = MockLLMUpstream("echo")
    apps = [{"app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
             "purposes": ["ticket_summary"], "restore_output": True, "models": ["mock-1"], "transparency": transparency,
             "tasks": {"ticket_summary": str(contract)}}]
    if mode == "production":
        cfg = production(tmp, apps, deployment=deployment)
    else:   # development: unsigned config, mock provider
        reg = tmp / "apps.yaml"
        apps[0]["tasks"] = {"ticket_summary": str(contract)}
        reg.write_text(yaml.safe_dump({"version": 1, "apps": apps}))
        cfg = dict(mode="development", registry_path=str(reg), policy_path=str(ROOT / "policies/hris-default.yaml"),
                   integrity_manifest=None)
        cfg["private_upstream"] = MockLLMUpstream("echo")
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=mock)
    return TestClient(create_app(s), raise_server_exceptions=False), mock


def send(client, text):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {"requester": {"name": "Ritika Banerjee"}}}
    return client.post("/v1/chat/completions", json=body, headers={
        "Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary", **attest("ticket_summary", body)})


def models(tmp_path_factory):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    pytest.importorskip("tokenizers")
    from tests import ml_fixtures as F
    d, sha = F.make_token_model(tmp_path_factory.mktemp("covmodel") / "pii")
    return F, d, sha


# --- startup rule -------------------------------------------------------------------------------------------------

def test_production_refuses_to_start_without_address_coverage_or_the_sealed_opt_out(tmp_path):
    with pytest.raises(CoverageError, match=r"requires_detectors.*pin_only"):
        gateway(tmp_path, {"address_coverage": None})


def test_the_sealed_pin_only_opt_out_starts_and_is_stated_on_healthz_and_in_every_report(tmp_path):
    client, mock = gateway(tmp_path, {"address_coverage": "pin_only"})
    h = client.get("/healthz").json()
    assert h["coverage"] == {"ADDRESS": "pin_only"}
    r = send(client, "Please summarise this ticket from Ritika about her payslip for March.")
    assert r.status_code == 200, r.text
    assert r.json()["pgw_report"]["coverage"] == {"ADDRESS": "pin_only"}


def test_the_opt_out_value_is_validated(tmp_path):
    with pytest.raises(ProfileError, match="address_coverage"):
        gateway(tmp_path, {"address_coverage": "yes"})


def test_a_model_label_mapped_to_address_satisfies_the_requirement(tmp_path, tmp_path_factory):
    F, d, sha = models(tmp_path_factory)
    det = F.detector_profile(d, sha, labels={"PER": "PERSON", "LOC": "ADDRESS"})
    client, _ = gateway(tmp_path, {"ml_detector": det, "ner_off_justification": None, "address_coverage": None})
    assert client.get("/healthz").json()["coverage"] == {"ADDRESS": "ml:" + sha[:12]}
    r = send(client, "Please summarise the note about priya nair.")
    assert r.status_code == 200, r.text
    assert r.json()["pgw_report"]["coverage"] == {"ADDRESS": "ml:" + sha[:12]}


def test_a_detector_without_an_address_label_does_not_satisfy_it(tmp_path, tmp_path_factory):
    F, d, sha = models(tmp_path_factory)
    det = F.detector_profile(d, sha)                    # LOC is IGNOREd: names only
    with pytest.raises(CoverageError):
        gateway(tmp_path, {"ml_detector": det, "ner_off_justification": None, "address_coverage": None})
    client, _ = gateway(tmp_path / "ack", {"ml_detector": det, "ner_off_justification": None,
                                           "address_coverage": "pin_only"})
    assert client.get("/healthz").json()["coverage"] == {"ADDRESS": "pin_only"}


def test_development_starts_without_coverage_with_a_warning(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        client, _ = gateway(tmp_path, mode="development")
    assert client.get("/healthz").json()["coverage"] == {"ADDRESS": "pin_only"}
    assert "address_coverage_pin_only_development" in caplog.text


# --- the contract declaration ---------------------------------------------------------------------------------------

def test_requires_detectors_default_and_declaration(tmp_path):
    raw = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    p = tmp_path / "c.yaml"

    def load(v="absent"):
        r = dict(raw)
        if v != "absent":
            r["requires_detectors"] = v
        p.write_text(yaml.safe_dump(r))
        return load_contract(p)
    assert load().requires_detectors == ("ADDRESS",)            # EXT_PSEUDO: free text reaches a model
    assert load([]).requires_detectors == ()                    # explicit, sealed with the contract
    assert load(["ADDRESS"]).requires_detectors == ("ADDRESS",)
    for bad in (["EMAIL"], "ADDRESS", ["ADDRESS", "ADDRESS"]):
        with pytest.raises(ContractError, match="requires_detectors"):
            load(bad)


def test_a_contract_declaring_no_requirement_starts_without_coverage(tmp_path):
    raw = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    raw["requires_detectors"] = []
    p = tmp_path / "src" / "ticket_summary.yaml"
    p.parent.mkdir()
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    client, _ = gateway(tmp_path, {"address_coverage": None}, contract=p)
    assert client.get("/healthz").json()["coverage"] == {"ADDRESS": "pin_only"}   # still stated, never hidden


# --- pin_only: the block a PIN ends -------------------------------------------------------------------------------

def spans(text, block):
    with places.pin_block(block):
        return sorted((d.start, d.end) for d in detect_d2(text) if d.type.value == "ADDRESS")


def covered(text, sp, piece):
    i = text.find(piece)
    return i >= 0 and any(s <= i and i + len(piece) <= e for s, e in sp)


def test_a_masked_pin_masks_the_street_block_next_to_it_in_the_gateway(tmp_path):
    client, mock = gateway(tmp_path, {"address_coverage": "pin_only"})
    r = send(client, f"Ritika lives at {STREET}. Please confirm the courier.")
    assert r.status_code == 200, r.text
    wire = mock.received[-1].decode().lower()
    for part in ("hanuman", "gandhi chowk", "koramangala", "560034"):
        assert part not in wire
    assert "[address_" in wire
    assert STREET in r.json()["choices"][0]["message"]["content"]       # restored exactly


def test_pin_block_is_off_outside_pin_only_scope():
    t = "Ritika lives at " + STREET
    assert not covered(t, spans(t, False), "Hanuman")
    assert covered(t, spans(t, True), "Hanuman")


def test_pin_block_coverage_on_generated_home_addresses():
    cases = home_addresses(120)

    def full(block):
        return sum(all(covered(c["text"], spans(c["text"], block), p) for p in c["parts"] + [c["pin"]]) for c in cases)
    before, after = full(False), full(True)
    assert before <= 0.1 * len(cases)                      # rule-based coverage alone: PIN masked, street raw
    structured = [c for i, c in enumerate(cases) if i % 8 < 6]       # capitalised pieces, commas or line breaks
    assert all(all(covered(c["text"], spans(c["text"], True), p) for p in c["parts"] + [c["pin"]]) for c in structured)
    assert after >= 0.7 * len(cases)
    # what structure cannot do: lower-case / comma-less writing keeps its street raw, and the docs say so
    assert after < len(cases)


def test_pin_block_does_not_swallow_prose_or_other_fields():
    t = "Thanks for the update. Owner: Operations, Pune 411038\nNext review: 15 July\n"
    sp = spans(t, True)
    assert sp and all("Next" not in t[s:e] and "Thanks" not in t[s:e] for s, e in sp)
    assert covered(t, sp, "411038")                        # never lost to a stronger neighbouring span
    t2 = "Ritika Banerjee\nFlat 4, Rose Villa, Adyar,\nChennai 600020\nPhone: 9845012345"
    sp2 = spans(t2, True)
    assert not any("Ritika" in t2[s:e] or "Phone" in t2[s:e] for s, e in sp2)


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_with_pins_and_six_digit_numbers_is_barely_touched(text):
    a, b = spans(text, False), spans(text, True)
    extra = sum(e - s for s, e in b) - sum(e - s for s, e in a)
    assert extra <= 60                                      # at most one short block; never a sentence
    for s, e in b:
        assert "." not in text[s:e].rstrip(".")[-1:] and "\n" not in text[s:e]


def test_six_digit_numbers_without_address_context_are_not_addresses():
    for t in ("Invoice 560034 for 450000 rupees, batch 123456.", "Total: 560034\nRef 400001\n", "Order #654321 shipped"):
        assert spans(t, True) == spans(t, False) == []


# --- round 9: separators, own-line PIN, lower case, no PIN; and the tab-row regression -------------------------------

from tests.address_cases import RESIDUAL_CATEGORIES, TAB_ROWS, VARIANT_CATEGORIES, variant_cases  # noqa: E402


def _pin_ok(c, sp):
    return not c["pin"] or covered(c["text"], sp, c["pin"])


def test_variant_layouts_are_covered_by_structure_alone():
    cases = variant_cases()
    assert len(cases) >= 150
    by = {k: [c for c in cases if c["cat"] == k] for k in VARIANT_CATEGORIES}
    for cat in ("pipe", "slash", "semi", "tab", "dash", "pin_own_line", "lower_comma", "nopin_house"):
        for c in by[cat]:
            sp = spans(c["text"], True)
            assert _pin_ok(c, sp) and all(covered(c["text"], sp, p) for p in c["parts"]), (cat, c["text"], sp)
    for c in by["lower_prose"]:                  # lower-case prose: the PIN only, and the words around it stay
        sp = spans(c["text"], True)
        assert _pin_ok(c, sp) and not any(covered(c["text"], sp, k) for k in c["keep"]), (c["text"], sp)
    for c in by["nopin_nohouse"]:                # no PIN, no house marker: nothing structural; stated in the docs
        assert spans(c["text"], True) == []
    assert set(RESIDUAL_CATEGORIES) == {"nopin_nohouse", "lower_prose"}


def test_ordinary_words_around_variant_addresses_are_not_swallowed():
    for c in variant_cases():
        sp = spans(c["text"], True)
        assert not any(covered(c["text"], sp, k) for k in c["keep"]), (c["text"], sp)


def test_a_pin_alone_on_a_line_needs_address_lines_above_it():
    assert spans("Amount\n560034\n", True) == []
    assert spans("Total payable\n560034\n", True) == []
    t = "Flat 4, Rose Villa,\nAdyar\nChennai\n600020\nThanks"
    sp = spans(t, True)
    assert covered(t, sp, "Rose Villa") and covered(t, sp, "600020") and not covered(t, sp, "Thanks")


def test_lower_case_text_is_taken_only_up_to_an_anchor():
    t0 = "thanks, call me tomorrow, delhi 110001 is the office"
    assert spans(t0, True) == [(t0.index("110001"), t0.index("110001") + 6)]
    t = "my address is 7 palm grove road, whitefield, bangalore 560066 is correct"
    sp = spans(t, True)
    assert covered(t, sp, "7 palm grove road, whitefield") and not covered(t, sp, "my address")


def _tab_wire(client, mock, row):
    r = send(client, "\t".join(row))
    assert r.status_code == 200, r.text
    return mock.received[-1].decode().lower()


@pytest.mark.parametrize("row", TAB_ROWS)
def test_a_tab_row_led_by_a_protected_name_keeps_street_and_pin_masked_in_pin_only_mode(tmp_path, row):
    """Round-9 regression: the PIN block ran left over the (tab-joined) name; the declared-name span overlapped it and
    the resolver dropped the whole block, so street AND PIN reached the provider."""
    client, mock = gateway(tmp_path, {"address_coverage": "pin_only"})
    wire = _tab_wire(client, mock, row)
    for part in (row[1].split(",")[0], row[3]):
        assert part.lower() not in wire, part
    assert "[address_" in wire and "[person_" in wire


@pytest.mark.parametrize("row", TAB_ROWS)
def test_a_tab_row_led_by_a_protected_name_keeps_the_street_masked_with_an_ml_address_label(tmp_path, tmp_path_factory, row):
    F, d, sha = models(tmp_path_factory)
    det = F.detector_profile(d, sha, labels={"PER": "PERSON", "LOC": "ADDRESS"})
    client, mock = gateway(tmp_path, {"ml_detector": det, "ner_off_justification": None, "address_coverage": None})
    wire = _tab_wire(client, mock, row)
    assert row[1].split(",")[0].lower() not in wire
    assert row[3] not in wire


def test_carve_blocks_splits_around_a_stronger_span_instead_of_dropping_the_block():
    from pgw.types import Detection, EntityType as T
    t = "Flat 4, c/o Ritika Banerjee, Sector 9, Noida, 201301"
    i = t.index("Ritika")
    dets = [Detection(0, len(t), T.ADDRESS, places.SCORE, places.SOURCE), Detection(i, i + 15, T.PERSON, 1.0, "C:declared")]
    with places.pin_block(True):
        out = places.carve_blocks(t, dets)
    pieces = sorted((d.start, d.end) for d in out if d.type is T.ADDRESS)
    assert pieces[0][0] == 0 and pieces[-1][1] == len(t) and all(e <= i or s >= i + 15 for s, e in pieces)
    assert places.carve_blocks(t, dets) is dets                      # no pin_block scope: unchanged


def test_benign_texts_keep_their_over_masking_bound_with_the_new_separators():
    for t in ["Q3 | Pune 411001 | 120000 units", "Status; Delhi 110001; approved", "Rows: a / b / Mumbai 400001",
              "Item\tQty\tRef\nBolt\t12\t560034\n", "Name\tCity\tPIN\nMeera\tPune\t411038\n"]:
        extra = sum(e - s for s, e in spans(t, True)) - sum(e - s for s, e in spans(t, False))
        assert extra <= 40, (t, spans(t, True))
