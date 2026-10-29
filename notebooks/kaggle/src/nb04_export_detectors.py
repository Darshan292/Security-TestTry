# %% [markdown]
# # 04 - Export open PII detectors to the gateway's ONNX artefact format and measure int8
# Candidates (all evaluated, none adopted): OpenAI Privacy Filter, ai4bharat IndicNER, a GLiNER PII model (Apache-2.0 card, **not**
# the CC-BY-NC `gliner_multi` v0), optionally a GLiNER2 PII model. For each: (1) licence read from the **model card** and asserted
# against the allowlist, (2) export to ONNX in the `pgw-ml-labels/1` format (token- or span-classification), (3) int8 dynamic
# quantisation, (4) fp32-vs-int8 agreement and recall on **synthetic dev text generated here** from `ml/train`, (5) CPU latency,
# (6) bundle + `sha256sums` + `MODEL_CARD.md`. A model that cannot be exported to a format the runtime supports is recorded as
# `unsupported` with the reason and the notebook continues: there is no fake success. Verification runs the exported files through
# the gateway's own runtime classes (`pgw.ml`), shipped in `pgw_ml_runtime.zip` by `scripts/ml/package_for_kaggle.py`.
# Settings: CPU or GPU T4 (export and int8 are CPU work), Internet **on**. Download the `detector_bundles` output and run
# `scripts/ml/bench_detectors.py` locally. **Synthetic data only; nothing from the repository's evaluation sets is used or uploaded.**

# %% include _common_env.py

# %% Configuration
SEED = 1234
N_DEV = int(os.environ.get("PGW_N_DEV", "300"))              # synthetic dev documents
N_LATENCY = int(os.environ.get("PGW_N_LAT", "60"))
ONLY = [x for x in os.environ.get("PGW_ONLY", "").split(",") if x]
GLINER2_REPO = os.environ.get("PGW_GLINER2_REPO", "")
ALLOW_OFFLINE_LICENCE_TABLE = os.environ.get("PGW_OFFLINE_LICENCE", "0") == "1"
INT8_MAX_DROP = 0.01
OUT = WORK / "detector_bundles"
OUT.mkdir(exist_ok=True)

# %% Install
pip_install("transformers>=4.41", "optimum[onnxruntime]>=1.20", "onnx", "onnxruntime", "huggingface_hub", "tokenizers",
            "sentencepiece", "protobuf", "gliner>=0.2.10")

# %% The gateway runtime slot (pgw.ml) from the attached dataset
rt_zips = find_files("pgw_ml_runtime.zip")
if rt_zips:
    RT = WORK / "pgw_rt"
    with zipfile.ZipFile(rt_zips[0]) as z:
        z.extractall(RT)
    sys.path.insert(0, str(RT))
try:
    from pgw.ml import artifact as gw_artifact
    from pgw.ml.span_model import OnnxSpanModel
    from pgw.ml.token_classifier import OnnxTokenClassifier
    RUNTIME_OK = True
except Exception as e:  # noqa: BLE001
    RUNTIME_OK = False
    print("WARNING: gateway runtime not importable (", type(e).__name__, e, "). Candidates cannot be verified and are reported "
          "as 'exported-unverified' at best. Rebuild the dataset with scripts/ml/package_for_kaggle.py.")

# %% Licence check (model card, per candidate)
from ml.train import card, detectors, licences  # noqa: E402

CANDS = {k: dict(v) for k, v in detectors.CANDIDATES.items() if not ONLY or k in ONLY}
if GLINER2_REPO:
    CANDS["gliner2_pii"]["repo"] = GLINER2_REPO
    licences.ALLOWED_DETECTORS.setdefault(GLINER2_REPO, "apache-2.0")
RESULTS = {}
for key, c in CANDS.items():
    rec = RESULTS[key] = {"key": key, "repo": c["repo"], "note": c["note"], "status": "pending"}
    if not c["repo"]:
        rec.update(status="skipped", reason="no repo configured (set PGW_GLINER2_REPO to include it)")
        continue
    try:
        rec["licence"] = licences.check_model(c["repo"], licences.hub_licence, allow_table_fallback=ALLOW_OFFLINE_LICENCE_TABLE,
                                              table=licences.ALLOWED_DETECTORS)
    except Exception as e:  # noqa: BLE001 (licence refusal is a result, not a crash)
        rec.update(status="licence_refused", reason=f"{type(e).__name__}: {e}"[:400])
        print("LICENCE REFUSED:", c["repo"], rec["reason"])
print({k: (v["status"], v.get("licence")) for k, v in RESULTS.items()})

# %% Synthetic dev text (generated in this notebook from ml/train)
from ml.train import synth  # noqa: E402

dev = synth.programmatic_records(N_DEV, SEED + 10)
DEV_TEXTS = [r["text"] for r in dev]
DEV_GOLD = [[(s["start"], s["end"], s["type"]) for s in r["spans"]] for r in dev]
print(len(dev), "synthetic dev docs;", sum(map(len, DEV_GOLD)), "gold spans")

# %% Helpers: export, int8, runtime verification
import copy  # noqa: E402
import inspect  # noqa: E402
import shutil  # noqa: E402
import traceback  # noqa: E402

import numpy as np  # noqa: E402
from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: E402


class Unsupported(Exception):
    """The model cannot be exported to a format the gateway runtime supports (recorded, not hidden)."""


def revision_of(repo):
    try:
        from huggingface_hub import model_info
        return model_info(repo).sha
    except Exception:  # noqa: BLE001
        return None


def kind_of(repo):
    from huggingface_hub import list_repo_files
    from transformers import AutoConfig
    files = set(list_repo_files(repo))
    if "gliner_config.json" in files:
        return "span"
    cfg = AutoConfig.from_pretrained(repo)
    archs = list(getattr(cfg, "architectures", None) or [])
    if any(a.endswith("ForTokenClassification") for a in archs):
        return "token"
    raise Unsupported(f"architectures {archs} (model_type {getattr(cfg, 'model_type', '?')}) are neither a token classifier nor "
                      "a GLiNER span model: no supported export")


def quantize(fp32, int8):
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8, per_channel=True, reduce_range=False,
                     extra_options={"MatMulConstBOnly": True})


def export_token(repo, d32):
    from optimum.onnxruntime import ORTModelForTokenClassification
    from transformers import AutoConfig, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(repo, use_fast=True)
    if not tok.is_fast:
        raise Unsupported("no fast tokenizer: tokenizer.json cannot be produced")
    cfg = AutoConfig.from_pretrained(repo)
    try:
        m = ORTModelForTokenClassification.from_pretrained(repo, export=True)
    except (ValueError, KeyError, NotImplementedError) as e:
        raise Unsupported(f"optimum cannot export this architecture to ONNX: {type(e).__name__}: {str(e)[:200]}") from None
    m.save_pretrained(str(d32 / "_hf"))
    shutil.copyfile(d32 / "_hf" / "model.onnx", d32 / "model.onnx")
    shutil.rmtree(d32 / "_hf")
    tok.backend_tokenizer.save(str(d32 / "tokenizer.json"))
    id2label = [cfg.id2label[i] for i in range(len(cfg.id2label))]
    scheme = "BIO" if any(x.startswith("B-") for x in id2label) else "IO"
    max_len = int(min(512, getattr(tok, "model_max_length", 512) if getattr(tok, "model_max_length", 512) < 100000 else 512))
    doc = card.token_labels_doc(cfg.id2label, tok, max_len, min(128, max_len // 4), scheme=scheme, aggregation="first")
    return doc, id2label


def export_span(repo, d32, prompts):
    from gliner import GLiNER
    model = GLiNER.from_pretrained(repo)
    fn = getattr(model, "export_to_onnx", None)
    if fn is None:
        raise Unsupported("the installed gliner package has no export_to_onnx")
    kw = {"save_dir": str(d32), "onnx_filename": "model.onnx", "quantize": False}
    kw = {k: v for k, v in kw.items() if k in inspect.signature(fn).parameters}
    fn(**kw)
    import onnx
    names = [i.name for i in onnx.load(str(d32 / "model.onnx"), load_external_data=False).graph.input]
    if set(names) != set(detectors.SPAN_INPUTS):
        raise Unsupported(f"exported graph inputs {names} differ from the six GLiNER v1 inputs {list(detectors.SPAN_INPUTS)}")
    tok = getattr(getattr(model, "data_processor", None), "transformer_tokenizer", None)
    if tok is None or not getattr(tok, "is_fast", False):
        raise Unsupported("no fast tokenizer on the GLiNER data processor: tokenizer.json cannot be produced")
    cfg = model.config
    ent, sep = getattr(cfg, "ent_token", "<<ENT>>"), getattr(cfg, "sep_token", "<<SEP>>")
    for t in (ent, sep):
        if tok.convert_tokens_to_ids(t) in (None, tok.unk_token_id):
            raise Unsupported(f"prompt token {t} is not in the tokenizer vocabulary")
    tok.backend_tokenizer.save(str(d32 / "tokenizer.json"))
    doc = {"format": card.LABELS_FORMAT, "task": "span-classification", "labels": list(prompts),
           "max_width": int(getattr(cfg, "max_width", 12)), "max_words": 256, "max_length": int(getattr(cfg, "max_len", 384)),
           "ent_token": ent, "sep_token": sep, "threshold": 0.5, "special_tokens": card._special_tokens(tok)}
    return doc


def runtime_for(dirpath):
    """The gateway's own loader: digest the directory, load it as an Artifact, build the runtime class."""
    digest, _ = gw_artifact.digest_dir(dirpath)
    art = gw_artifact.load(dirpath, digest)
    cls = OnnxTokenClassifier if art.task == "token-classification" else OnnxSpanModel
    return cls(art, intra_op_threads=1, inter_op_threads=1, batch_size=8)


def run_dev(det, mapping):
    preds, ms = [], []
    for t in DEV_TEXTS:
        t0 = time.perf_counter()
        spans = det.detect(t)
        ms.append((time.perf_counter() - t0) * 1000)
        preds.append([(s.start, s.end, mapping.get(s.type, "IGNORE"), s.score) for s in spans if mapping.get(s.type, "IGNORE") != "IGNORE"])
    return preds, ms


def assemble(d, files_from, model_src, labels_doc):
    d.mkdir(parents=True, exist_ok=True)
    for f in ("tokenizer.json",):
        shutil.copyfile(files_from / f, d / f)
    shutil.copyfile(model_src, d / "model.onnx")
    card.write_labels(d / "labels.json", labels_doc)
    (d / "MODEL_CARD.md").write_text("# placeholder, replaced below\n", encoding="utf-8")

# %% Process every candidate (failures are recorded and the loop continues)
for key, c in CANDS.items():
    rec = RESULTS[key]
    if rec["status"] != "pending":
        continue
    work = WORK / "export" / key
    shutil.rmtree(work, ignore_errors=True)
    d32, d8 = work / "fp32", work / "int8"
    d32.mkdir(parents=True)
    try:
        rec["revision"] = revision_of(c["repo"])
        kind = kind_of(c["repo"]) if c["kind"] == "auto" else c["kind"]
        if kind == "unsupported":
            raise Unsupported(c["note"])
        if kind == "token":
            labels_doc, id2label = export_token(c["repo"], d32)
            mapping, unknown = detectors.map_token_labels(id2label, c["map"])
            backend = "token"
        else:
            prompts = c["prompts"] if "prompts" in c else None
            if prompts is None:
                raise Unsupported("a GLiNER-style model needs a reviewed prompt/label table (none for this candidate)")
            labels_doc = export_span(c["repo"], d32, prompts)
            mapping, unknown = detectors.span_prompt_map(prompts), []
            backend = "span"
        rec["kind"], rec["labels"], rec["mapping"], rec["unmapped_labels"] = kind, list(mapping), mapping, unknown
        if not RUNTIME_OK:
            rec.update(status="exported-unverified", reason="gateway runtime unavailable: files written but not loaded")
            continue
        # fp32 directory in bundle layout, then int8
        assemble(d32 / "b", d32, d32 / "model.onnx", labels_doc)
        d8.mkdir(parents=True, exist_ok=True)
        quantize(d32 / "model.onnx", d8 / "model.onnx")
        assemble(d8 / "b", d32, d8 / "model.onnx", labels_doc)
        fp, int8 = runtime_for(d32 / "b"), runtime_for(d8 / "b")
        fp.warm(), int8.warm()
        p32, ms32 = run_dev(fp, mapping)
        p8, ms8 = run_dev(int8, mapping)
        r32, r8 = detectors.gold_recall(DEV_GOLD, p32), detectors.gold_recall(DEV_GOLD, p8)
        drop = card.max_recall_drop(r32, r8)
        rec["dev"] = {"fp32_gold_recall": r32, "int8_gold_recall": r8, "agreement_int8_vs_fp32": detectors.span_agreement(p32, p8),
                      "int8_max_recall_drop": drop, "latency_ms_per_doc_fp32": detectors.latency_stats(ms32[:N_LATENCY]),
                      "latency_ms_per_doc_int8": detectors.latency_stats(ms8[:N_LATENCY]), "cpu": os.cpu_count(),
                      "fp32_mb": round((d32 / "model.onnx").stat().st_size / 1e6, 1), "int8_mb": round((d8 / "model.onnx").stat().st_size / 1e6, 1)}
        use8 = drop <= INT8_MAX_DROP
        rec["shipped_precision"] = "int8" if use8 else "fp32"
        if not use8:
            rec["note_int8"] = f"int8 lost {drop * 100:.2f} points of dev recall (> {INT8_MAX_DROP * 100:.0f}); the fp32 graph is shipped"
        final = OUT / key
        shutil.rmtree(final, ignore_errors=True)
        assemble(final, d32, (d8 if use8 else d32) / "model.onnx", labels_doc)
        card.write_train_meta(final / "train_meta.json", detectors.bench_meta(mapping, backend, labels_doc.get("threshold")))
        metrics = {"training_data": "third-party pretrained (not ours); synthetic-dev evaluation only", "real_data_validated": False,
                   "licence": rec["licence"], "repo": c["repo"], "revision": rec["revision"], "shipped_precision": rec["shipped_precision"],
                   "int8_vs_fp32": {"max_recall_drop": drop, "gate_points": INT8_MAX_DROP * 100}, "dev": rec["dev"]}
        (final / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
        detectors.write_card(final / "MODEL_CARD.md", title=f"{c['repo']} exported for the PGW runtime slot", repo=c["repo"],
                             revision=rec["revision"], licence=rec["licence"], allowed=licences.ALLOWED_LICENCES,
                             export=f"{kind} model -> ONNX ({rec['shipped_precision']}), labels.json pgw-ml-labels/1, loaded and run by the gateway runtime class.",
                             mapping=mapping, unknown=unknown, metrics=rec["dev"])
        card.check_bundle_dir(final)
        card.write_sha256sums(final)
        gw_artifact.digest_dir(final)       # the runtime's own structural check on the final bundle
        rec.update(status="exported", manifest_digest=gw_artifact.digest_dir(final)[0])
    except Unsupported as e:
        rec.update(status="unsupported", reason=str(e)[:600])
        print(f"[{key}] UNSUPPORTED: {rec['reason']}")
    except Exception as e:  # noqa: BLE001
        rec.update(status="error", reason=f"{type(e).__name__}: {e}"[:600])
        print(f"[{key}] ERROR: {rec['reason']}")
        traceback.print_exc()

# %% Summary and downloads
summary = {k: {kk: v.get(kk) for kk in ("repo", "licence", "status", "reason", "shipped_precision", "manifest_digest", "unmapped_labels")}
           for k, v in RESULTS.items()}
print(json.dumps(summary, indent=2))
for k, v in RESULTS.items():
    if v["status"] == "exported":
        dv = v["dev"]
        print(f"{k}: recall fp32 {dv['fp32_gold_recall']['micro']} int8 {dv['int8_gold_recall']['micro']} | agreement exact "
              f"{dv['agreement_int8_vs_fp32']['exact_recall']} | p50 ms int8 {dv['latency_ms_per_doc_int8'].get('p50')} fp32 {dv['latency_ms_per_doc_fp32'].get('p50')}")
(OUT / "results.json").write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
print("Download the 'detector_bundles' folder (one subfolder per exported model + results.json) and run scripts/ml/bench_detectors.py.")
