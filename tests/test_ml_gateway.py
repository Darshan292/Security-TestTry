"""Gateway integration of the ML layer, in PRODUCTION mode with a signed profile: the model pin stops startup on a
mismatch, ML failure fails the request closed, the ML detector can replace spaCy, the heuristics switch is signed, the
content gate keeps signed special-category requests in-house (or refuses them) and is reported, and the private
endpoint probe accepts llama.cpp's llama-server. Tiny models generated at test time; synthetic data only."""
import hashlib
import os
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")
pytest.importorskip("tokenizers")

from pgw.gateway.app import create_app, probe_private  # noqa: E402
from pgw.gateway.settings import ProfileError, Settings  # noqa: E402
from pgw.gateway.upstream import MockLLMUpstream, private_identity  # noqa: E402
from pgw.ml.artifact import ArtifactError  # noqa: E402
from pgw.ml.base import MlUnavailable  # noqa: E402
from pgw.ml.runtime import GateRunner, MlEntityStage  # noqa: E402
from pgw.product.contracts import ContractError, load_contract  # noqa: E402
from tests import ml_fixtures as F  # noqa: E402
from tests.sealed import attest, production  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEY = "ml-app-key"
TICKET = "Please summarise: priya nair asked about her payslip for March and the team meeting."
SICK = "Please summarise: priya nair shared her cancer diagnosis and asked about leave."


def gated_contract(tmp: Path, action: str = "PRIVATE", labels=("health",), name="ticket_summary") -> Path:
    raw = yaml.safe_load((ROOT / "contracts/ticket_summary.yaml").read_text())
    raw["content_gate"] = {"labels": list(labels), "threshold": 0.5, "action": action}
    p = tmp / "src" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    return p


def apps(contract: Path | None = None):
    return [{"app_id": "pilot", "tenant": "acme", "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
             "restore_output": True, "models": ["mock-1"], "purposes": ["ticket_summary"], "transparency": True,
             "tasks": {"ticket_summary": str(contract or ROOT / "contracts/ticket_summary.yaml")}}]


def gateway(tmp, deployment, contract=None, private=None):
    ext = MockLLMUpstream("summarise")
    priv = private or MockLLMUpstream("summarise")
    cfg = production(tmp, apps(contract), deployment=deployment, private_upstream=priv)
    s = Settings(**cfg, ledger_db_path=str(tmp / "l.sqlite"), egress_ledger_path=str(tmp / "e.jsonl"),
                 master_key=os.urandom(32).hex(), upstream=ext)
    return TestClient(create_app(s)), ext, priv


def send(client, text, signed=True):
    body = {"model": "mock-1", "messages": [{"role": "user", "content": text}],
            "pgw_subjects": {"requester": {"name": "Imran Ansari"}}}
    h = {"Authorization": f"Bearer {KEY}", "X-PGW-Purpose": "ticket_summary"}
    if signed:
        h.update(attest("ticket_summary", body))
    return client.post("/v1/chat/completions", json=body, headers=h)


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    base = tmp_path_factory.mktemp("models")
    return {"tok": F.make_token_model(base / "pii"), "gate": F.make_gate_model(base / "gate")}


# --- ML detector ----------------------------------------------------------------------------------------------------

def test_ml_detector_replaces_spacy_without_justification_and_protects(tmp_path, models):
    det = F.detector_profile(*models["tok"])
    client, ext, _ = gateway(tmp_path, {"ml_detector": det, "ner_off_justification": None, "legacy_heuristics": False})
    r = send(client, TICKET)
    assert r.status_code == 200, r.text
    sent = ext.received[-1].decode().lower()
    assert "priya" not in sent and "nair" not in sent              # found by the model (lower case, no cue)
    rep = r.json()["pgw_report"]
    assert "content_gate" not in [s["id"] for s in rep["stages"]]
    assert "content_gate" not in [c["id"] for c in rep["checks"]]  # report unchanged without a gate


def test_ml_detector_in_worker_processes(tmp_path, models):
    det = F.detector_profile(*models["tok"], workers=1)
    client, ext, _ = gateway(tmp_path, {"ml_detector": det, "ner_off_justification": None})
    with client:
        assert send(client, TICKET).status_code == 200
        assert "priya" not in ext.received[-1].decode().lower()


def test_a_model_that_does_not_match_its_pin_stops_startup(tmp_path, models):
    d, sha = models["tok"]
    with pytest.raises(ArtifactError, match="does not match the sha256"):
        gateway(tmp_path, {"ml_detector": F.detector_profile(d, "e" * 64), "ner_off_justification": None})


def test_ml_failure_fails_the_request_closed(tmp_path, models, monkeypatch):
    client, ext, _ = gateway(tmp_path, {"ml_detector": F.detector_profile(*models["tok"]),
                                        "ner_off_justification": None})

    def down(self, texts):
        raise MlUnavailable("model down")
    monkeypatch.setattr(MlEntityStage, "detect_many", down)
    r = send(client, TICKET)
    assert r.status_code == 503 and r.json()["error"]["type"] == "detector_unavailable" and not ext.received


def test_heuristics_off_needs_an_ml_detector_in_the_signed_profile(tmp_path):
    with pytest.raises(ProfileError, match="legacy_heuristics"):
        gateway(tmp_path, {"legacy_heuristics": False})


def test_ner_off_without_ml_still_needs_a_justification(tmp_path):
    with pytest.raises(ProfileError, match="ner_off_justification"):
        gateway(tmp_path, {"ner_off_justification": None})


def test_ml_settings_cannot_come_from_outside_the_profile(tmp_path, models):
    cfg = production(tmp_path, apps())
    with pytest.raises(ProfileError, match="ml_detector"):
        create_app(Settings(**cfg, ledger_db_path=str(tmp_path / "l"), egress_ledger_path=str(tmp_path / "e"),
                            master_key=os.urandom(32).hex(), upstream=MockLLMUpstream("summarise"),
                            ml_detector=F.detector_profile(*models["tok"])))


def test_a_malformed_ml_profile_entry_stops_startup(tmp_path, models):
    bad = {**F.detector_profile(*models["tok"]), "labels": {"PER": "NOT_A_TYPE"}}
    with pytest.raises(ProfileError, match="ml_detector"):
        gateway(tmp_path, {"ml_detector": bad, "ner_off_justification": None})


# --- content gate ----------------------------------------------------------------------------------------------------

def gate_deployment(models, **kw):
    return {"content_gate_model": F.gate_profile(*models["gate"], **kw)}


def test_signed_special_category_request_stays_in_house(tmp_path, models):
    client, ext, priv = gateway(tmp_path, gate_deployment(models), gated_contract(tmp_path, "PRIVATE"))
    ok = send(client, TICKET)
    assert ok.status_code == 200 and len(ext.received) == 1 and not priv.received
    rep = ok.json()["pgw_report"]
    assert {"id": "content_gate", "status": "done"} == {k: v for k, v in rep["stages"][2].items() if k != "ms"}
    assert next(c for c in rep["checks"] if c["id"] == "content_gate")["status"] == "pass"
    r = send(client, SICK)                         # validly signed, but a health topic: in-house only
    assert r.status_code == 200, r.text
    assert len(ext.received) == 1 and len(priv.received) == 1
    assert r.headers["X-PGW-Route"] == "PRIVATE_LOCAL"
    rep = r.json()["pgw_report"]
    assert rep["route"] == "PRIVATE_LOCAL" and rep["outcome"] == "kept_inside"
    assert next(c for c in rep["checks"] if c["id"] == "content_gate")["status"] == "fail"
    assert next(c for c in rep["checks"] if c["id"] == "source_verified")["status"] == "pass"


def test_gate_refuse_action(tmp_path, models):
    client, ext, priv = gateway(tmp_path, gate_deployment(models), gated_contract(tmp_path, "REFUSE"))
    r = send(client, SICK)
    assert r.status_code == 422 and r.json()["error"]["type"] == "special_category_content"
    assert not ext.received and not priv.received
    assert "cancer" not in r.text                 # no content in the error
    assert send(client, TICKET).status_code == 200


def test_gate_failure_fails_closed(tmp_path, models, monkeypatch):
    client, ext, priv = gateway(tmp_path, gate_deployment(models), gated_contract(tmp_path, "PRIVATE"))

    def down(self, texts):
        raise MlUnavailable("gate down")
    monkeypatch.setattr(GateRunner, "classify_many", down)
    r = send(client, TICKET)
    assert r.status_code == 503 and r.json()["error"]["type"] == "content_gate_unavailable"
    assert not ext.received and not priv.received


def test_gate_in_worker_processes(tmp_path, models):
    client, ext, priv = gateway(tmp_path, gate_deployment(models, workers=1), gated_contract(tmp_path, "PRIVATE"))
    with client:
        assert send(client, SICK).status_code == 200 and priv.received and not ext.received


def test_a_gated_contract_without_a_gate_model_stops_startup(tmp_path):
    with pytest.raises(ContractError, match="content_gate_model"):
        gateway(tmp_path, {}, gated_contract(tmp_path, "PRIVATE"))


def test_gate_labels_must_exist_in_the_model(tmp_path, models):
    with pytest.raises(ContractError, match="not labels"):
        gateway(tmp_path, gate_deployment(models), gated_contract(tmp_path, "PRIVATE", labels=("religion",)))


def test_gate_private_action_needs_a_private_endpoint_and_route(tmp_path, models):
    raw = yaml.safe_load((ROOT / "contracts/salary_comparison.yaml").read_text())
    raw["content_gate"] = {"labels": ["health"], "threshold": 0.5, "action": "PRIVATE"}
    p = tmp_path / "lc.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ContractError, match="EXT_PSEUDO"):
        load_contract(p)


# --- private endpoint: llama.cpp llama-server identity ---------------------------------------------------------------

LLAMA_SERVER_MODELS = {   # GET /v1/models of llama-server (--alias qwen2.5-7b-instruct), recent builds
    "models": [{"name": "qwen2.5-7b-instruct", "model": "qwen2.5-7b-instruct", "modified_at": "", "size": "",
                "digest": "", "type": "model", "description": "", "tags": [""], "capabilities": ["completion"],
                "parameters": "", "details": {"parent_model": "", "format": "gguf", "family": "", "families": [""],
                                              "parameter_size": "", "quantization_level": ""}}],
    "object": "list",
    "data": [{"id": "qwen2.5-7b-instruct", "object": "model", "created": 1767225600, "owned_by": "llamacpp",
              "meta": {"vocab_type": 2, "n_vocab": 152064, "n_ctx_train": 32768, "n_embd": 3584,
                       "n_params": 7615616512, "size": 4677120000}}]}


class _Probe:
    def __init__(self, payload):
        self.payload = payload

    def identify(self):
        return self.payload


def test_llama_server_identity_is_accepted_and_the_standin_still_refused():
    assert private_identity(LLAMA_SERVER_MODELS) == ("model", ("qwen2.5-7b-instruct",))
    assert probe_private(_Probe(LLAMA_SERVER_MODELS), "qwen2.5-7b-instruct", production=True) == "model"
    path_id = {"object": "list", "data": [{"id": "/models/model.gguf", "object": "model", "owned_by": "llamacpp"}]}
    assert probe_private(_Probe(path_id), "/models/model.gguf", production=True) == "model"
    from pgw import private_llm
    from starlette.testclient import TestClient as TC
    standin = TC(private_llm.build_app(private_llm._Extractive())).get("/v1/models").json()
    with pytest.raises(ValueError, match="stand-in"):
        probe_private(_Probe(standin), "local", production=True)
    disguised = {**LLAMA_SERVER_MODELS, "data": LLAMA_SERVER_MODELS["data"] + [standin["data"][0]]}
    with pytest.raises(ValueError, match="stand-in"):
        probe_private(_Probe(disguised), "qwen2.5-7b-instruct", production=True)
