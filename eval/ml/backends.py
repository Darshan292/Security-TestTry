"""Backends under evaluation. Each takes a text and returns (predicted spans, protected text).

  current      today's stack: D0-D5 regex, heuristics, roster (if given), no NER (spaCy with --ner MODEL)
  pipeline_ml  today's stack plus the ML detector (precedence: checksummed-ID regex > ML > heuristics)
  ml_checksum  the ML detector + ID regex + roster, legacy name/amount heuristics OFF (`legacy_heuristics: false`)
  ml_only      the ML detector alone (its spans masked as [TYPE]); for model selection on dev

The ML model is given as a JSON/YAML file with the same mapping as the profile's `ml_detector` (path, sha256,
labels, min_score_by_type, ...): the artefact is verified against its pin exactly as in production.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from pgw.detect.edm import RosterIndex
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.types import Context

ROOT = Path(__file__).resolve().parents[2]
CTX = Context("eval", "eval-harness", "eval_tokenize_all", conversation_id=None, restore_output=True)
BACKENDS = ("current", "pipeline_ml", "ml_checksum", "ml_only")


def load_model_config(path: str | Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    return json.loads(raw) if str(path).endswith(".json") else yaml.safe_load(raw)


class Backend:
    def __init__(self, name: str, model_cfg: dict | None = None, roster: str | None = None, ner: str | None = None):
        if name not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}")
        if name != "current" and model_cfg is None:
            raise ValueError(f"backend {name} needs --model")
        self.name = name
        self.ml = None
        if model_cfg is not None:
            from pgw.ml.config import parse_detector
            from pgw.ml.runtime import build_detector
            cfg = parse_detector(model_cfg)
            self.ml = build_detector(cfg.as_mapping())
            self.ml.warm()
            self.model_sha256 = cfg.sha256
        keys = Keys(bytes(32))
        rost = None
        if roster:
            rost = RosterIndex(keys.derive("eval", "edm"))
            for line in Path(roster).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rost.add_employee(json.loads(line))
        ner_det = None
        if ner:
            from pgw.detect.ner import NerDetector
            ner_det = NerDetector(ner)
        policy = PolicyPack.load(ROOT / "policies/hris-default.yaml")
        if name != "ml_only":
            self.prot = Protector(policy, keys, rost, ner=ner_det, ml=self.ml if name != "current" else None,
                                  heuristics=name != "ml_checksum")

    def run(self, text: str) -> tuple[list[tuple[int, int, str]], str]:
        if self.name == "ml_only":
            dets = self.ml.detect(text)
            prot = text
            for d in sorted(dets, key=lambda d: d.start, reverse=True):
                prot = prot[:d.start] + f"[{d.type.value}]" + prot[d.end:]
            return [(d.start, d.end, d.type.value) for d in dets], prot
        msgs = [{"role": "user", "content": text}]
        p = self.prot.protect(msgs, CTX)
        dets = p.detections[0] if p.detections else []
        return [(d.start, d.end, d.type.value) for d in dets], p.messages[0]["content"]
