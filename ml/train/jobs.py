"""Deterministic generation jobs for the LLM template writer: job i always means the same genre and controls for a
given seed, so a resumed notebook skips finished jobs and never repeats or reorders work."""
from __future__ import annotations

import dataclasses

from . import gate, synth
from .genres import BY_NAME, GENRES, Controls, sample_controls

TEMPERATURES = (0.7, 0.8, 0.9, 1.0, 1.1)


def template_job(seed: int, i: int, allow: tuple[str, ...] | None = None, per_call: int = 3) -> dict:
    rng = synth.derive_rng(seed, "tjob", i)
    pool = [g for g in GENRES if allow is None or g.name in allow]
    spec = rng.choices(pool, weights=[g.weight for g in pool], k=1)[0]
    ctl = sample_controls(rng, spec)
    ctl = dataclasses.replace(ctl, temperature=TEMPERATURES[i // 8 % len(TEMPERATURES)])  # 8 consecutive jobs share a temperature (HF batches)
    return {"job": i, "kind": "template", "genre": spec.name, "lang": ctl.lang, "controls": dataclasses.asdict(ctl),
            "messages": synth.build_messages(spec, Controls(**{**dataclasses.asdict(ctl), "extra_negatives": tuple(ctl.extra_negatives)}), per_call),
            "max_new_tokens": {"short": 900, "medium": 1700, "long": 3000}[ctl.length] if per_call >= 3 else 1500}


def gate_job(seed: int, i: int, per_call: int = 4) -> dict:
    rng = synth.derive_rng(seed, "gjob", i)
    labels = gate.sample_label_set(rng)
    lang = rng.choice(("en", "en", "en", "hi-Latn", "en-hi"))
    return {"job": i, "kind": "gate", "labels": labels, "lang": lang, "genre": "gate",
            "controls": {"temperature": TEMPERATURES[i // 8 % len(TEMPERATURES)]},
            "messages": gate.build_gate_messages(labels, rng, lang, per_call), "max_new_tokens": 900}


def accept(job: dict, raw: str, model: str, detector=None) -> tuple[list[dict], list[str]]:
    """Parse and validate one model reply -> (accepted template records, reject reasons)."""
    ok, reasons = [], []
    spec = BY_NAME.get(job["genre"])
    for k, tpl in enumerate(synth.parse_llm_output(raw)):
        if job["kind"] == "gate":
            good, why = gate.validate_gate_template(tpl, job["labels"])
        else:
            good, why = synth.validate_template(tpl, spec, detector)
        if good:
            rec = {"id": f"{'g' if job['kind'] == 'gate' else 't'}{job['job']:06d}-{k}", "genre": job["genre"], "lang": job["lang"],
                   "template": tpl, "model": model, "job": job["job"]}
            if job["kind"] == "gate":
                rec["labels"] = job["labels"]
            else:
                rec["controls"] = {k2: v for k2, v in job["controls"].items() if k2 != "extra_negatives"}
            ok.append(rec)
        else:
            reasons.append(why.split(":")[0][:60])
    return ok, reasons
