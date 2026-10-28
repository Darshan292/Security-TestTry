# %% [markdown]
# # 02 - Train the token classifier (PII / identifier NER)
# xlm-roberta-base (MIT; alternate google/muril-base-cased, Apache-2.0), fp16, 512-token windows with stride, early stopping on
# dev entity F1, recall-first reporting, ONNX export, dynamic int8 quantisation, **int8-vs-fp32 recall gate** (fails the notebook
# if int8 loses more than 1 point of recall), CPU latency, model card. Settings: GPU T4 x2 (or P100), Internet **on**.
# Inputs: the `ml/train` dataset and the cleaned templates dataset (templates.jsonl). Without templates it trains on the
# programmatic genres only.

# %% include _common_env.py

# %% Configuration
SEED = 1234
BASE_MODEL = os.environ.get("PGW_BASE", "FacebookAI/xlm-roberta-base")      # alternate: google/muril-base-cased
N_DOCS = int(os.environ.get("PGW_N_DOCS", "60000"))
PROGRAMMATIC_FRACTION = float(os.environ.get("PGW_PROG_FRAC", "0.35"))
DEV_TEMPLATE_FRACTION = 0.08
DEV_DOCS_PER_TEMPLATE = 3
MAX_LEN, STRIDE = 512, 128
EPOCHS, LR, WARMUP = 4, 3e-5, 0.06
PER_DEVICE_BATCH, GRAD_ACCUM = 8, 4
LOSS = os.environ.get("PGW_LOSS", "weighted")        # "ce" | "weighted" | "focal"
OPERATING_TAU = 0.3                                   # recall-oriented decision threshold: entity if P(not O) >= tau
TAUS = (0.5, 0.4, 0.3, 0.2, 0.1, 0.05)
ALLOW_OFFLINE_LICENCE_TABLE = os.environ.get("PGW_OFFLINE_LICENCE", "0") == "1"
OUT = WORK / "token_classifier"
DATA = WORK / "data"
CKPT = WORK / "ckpt"
for d in (OUT, DATA, CKPT):
    d.mkdir(exist_ok=True)

# %% Install and licence check
pip_install("transformers>=4.41", "accelerate", "seqeval", "optimum[onnxruntime]>=1.20", "onnx", "onnxruntime", "huggingface_hub")
from ml.train import licences  # noqa: E402

LICENCE = licences.check_model(BASE_MODEL, licences.hub_licence, allow_table_fallback=ALLOW_OFFLINE_LICENCE_TABLE)

# %% Build the data: fill templates + programmatic genres, split BY TEMPLATE
import collections  # noqa: E402
import math  # noqa: E402

from ml.train import io as mio, synth  # noqa: E402
from ml.train.labels import BIO_LABELS, ENTITY_TYPES, ID2LABEL, LABEL2ID  # noqa: E402

tpl_files = find_files("templates.jsonl")
templates = [t for p in tpl_files[:1] for t in mio.read_jsonl(p)]
print("templates loaded:", len(templates), "from", tpl_files[:1])
train_ids, dev_ids = synth.split_templates([t["id"] for t in templates], DEV_TEMPLATE_FRACTION, SEED) if templates else (set(), set())
assert not (train_ids & dev_ids), "template leakage between train and dev"
n_prog = int(N_DOCS * PROGRAMMATIC_FRACTION) if templates else N_DOCS
n_llm = N_DOCS - n_prog
train_t = [t for t in templates if t["id"] in train_ids]
dev_t = [t for t in templates if t["id"] in dev_ids]
per_tpl = max(1, math.ceil(n_llm / max(len(train_t), 1))) if train_t else 0
train_recs = synth.fill_records(train_t, per_tpl, SEED)[:n_llm] if train_t else []
dev_recs = synth.fill_records(dev_t, DEV_DOCS_PER_TEMPLATE, SEED + 1) if dev_t else []
# programmatic documents: independent draws, split by index (train seed != dev seed, so values never repeat on purpose)
prog_train = synth.programmatic_records(n_prog, SEED + 2)
prog_dev = synth.programmatic_records(max(300, int(0.08 * n_prog)), SEED + 3)
train_recs += prog_train
dev_recs += prog_dev
import random as _r  # noqa: E402

_r.Random(SEED).shuffle(train_recs)
mio.write_jsonl(DATA / "train.jsonl", train_recs)
mio.write_jsonl(DATA / "dev.jsonl", dev_recs)
assert not ({r.get("template_id") for r in train_recs} & {r.get("template_id") for r in dev_recs} - {None})
print(f"train docs {len(train_recs)} | dev docs {len(dev_recs)}")
cnt = collections.Counter(s["type"] for r in train_recs for s in r["spans"])
print("train entities per type:", dict(cnt))
print("train docs per genre:", dict(collections.Counter(r["genre"] for r in train_recs)))
missing = [t for t in ENTITY_TYPES if cnt[t] < 500]
if missing:
    print("WARNING: rare types (<500 train spans):", missing)

# %% Tokenise into windows
from transformers import AutoModelForTokenClassification, AutoTokenizer  # noqa: E402

from ml.train import trainprep  # noqa: E402

tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, use_fast=True)
assert tokenizer.is_fast, "a fast tokenizer is required for offset mapping"
train_rows, drop_t = trainprep.encode_records(train_recs, tokenizer, MAX_LEN, STRIDE)
dev_rows, drop_d = trainprep.encode_records(dev_recs, tokenizer, MAX_LEN, STRIDE)
print(f"windows: train {len(train_rows)} (dropped {drop_t} docs with token/entity straddle), dev {len(dev_rows)} (dropped {drop_d})")
assert drop_t < 0.01 * len(train_recs), "too many documents dropped: check tokenizer/offset behaviour"

# %% Model, loss options, metrics
import numpy as np  # noqa: E402
import torch  # noqa: E402
from seqeval.metrics import f1_score as seq_f1  # noqa: E402
from torch.utils.data import Dataset  # noqa: E402
from transformers import (DataCollatorForTokenClassification, EarlyStoppingCallback, Trainer, TrainingArguments)  # noqa: E402


class Rows(Dataset):
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]


weights = torch.tensor(trainprep.class_weights(train_rows, len(BIO_LABELS)), dtype=torch.float32)


class LossTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        out = model(**inputs)
        logits = out.logits.float()
        flat, y = logits.view(-1, logits.size(-1)), labels.view(-1)
        if LOSS == "ce":
            loss = torch.nn.functional.cross_entropy(flat, y, ignore_index=-100)
        elif LOSS == "weighted":
            loss = torch.nn.functional.cross_entropy(flat, y, weight=weights.to(flat.device), ignore_index=-100)
        elif LOSS == "focal":
            ce = torch.nn.functional.cross_entropy(flat, y, weight=weights.to(flat.device), ignore_index=-100, reduction="none")
            pt = torch.exp(-ce)
            keep = y != -100
            loss = (((1 - pt) ** 2) * ce)[keep].mean()
        else:
            raise ValueError(LOSS)
        return (loss, out) if return_outputs else loss


def compute_metrics(p):
    logits, labels = p
    pred = logits.argmax(-1)
    gold, hyp = [], []
    for pr, lb in zip(pred, labels, strict=True):
        keep = lb != -100
        gold.append([ID2LABEL[int(x)] for x in lb[keep]])
        hyp.append([ID2LABEL[int(x)] for x in pr[keep]])
    return {"f1": seq_f1(gold, hyp)}


model = AutoModelForTokenClassification.from_pretrained(BASE_MODEL, num_labels=len(BIO_LABELS), id2label=ID2LABEL, label2id=LABEL2ID)
args = TrainingArguments(
    output_dir=str(CKPT), num_train_epochs=EPOCHS, learning_rate=LR, warmup_ratio=WARMUP, weight_decay=0.01,
    per_device_train_batch_size=PER_DEVICE_BATCH, per_device_eval_batch_size=PER_DEVICE_BATCH * 2, gradient_accumulation_steps=GRAD_ACCUM,
    fp16=torch.cuda.is_available(), eval_strategy="epoch", save_strategy="epoch", save_total_limit=2, load_best_model_at_end=True,
    metric_for_best_model="f1", greater_is_better=True, logging_steps=100, report_to="none", seed=SEED, dataloader_num_workers=2,
    remove_unused_columns=False, eval_accumulation_steps=16)
trainer = LossTrainer(model=model, args=args, train_dataset=Rows(train_rows), eval_dataset=Rows(dev_rows),
                      data_collator=DataCollatorForTokenClassification(tokenizer, pad_to_multiple_of=8), compute_metrics=compute_metrics,
                      callbacks=[EarlyStoppingCallback(early_stopping_patience=2)])

# %% Train (resumable: checkpoints in WORK/ckpt; to continue a killed session add its output as an input)
resume = None
for root in (CKPT, INPUT):
    found = sorted(root.rglob("checkpoint-*/trainer_state.json"), key=lambda p: int(p.parent.name.split("-")[1]))
    if found:
        resume = str(found[-1].parent)
        break
print("resuming from", resume)
trainer.train(resume_from_checkpoint=resume)
best_dir = OUT / "hf"
trainer.save_model(str(best_dir))
tokenizer.save_pretrained(str(best_dir))

# %% Dev evaluation helpers (same code scores fp32 torch and int8 ONNX)
from ml.train import card, infer, metrics  # noqa: E402
from ml.train.bio import bio_to_spans  # noqa: E402

dev_eval_docs = dev_recs[: min(len(dev_recs), 3000)]


def evaluate(logits_fn, label):
    probs = [infer.doc_probs(r["text"], tokenizer, logits_fn, MAX_LEN, STRIDE, batch=8) for r in dev_eval_docs]
    gold = [r["spans"] for r in dev_eval_docs]
    by_tau = {}
    for tau in TAUS:
        by_tau[tau] = [bio_to_spans(r["text"], metrics.decode_with_threshold(p, tau), offs) for r, (p, offs) in zip(dev_eval_docs, probs, strict=True)]
    strict = metrics.entity_prf(gold, by_tau[OPERATING_TAU])
    lenient = metrics.entity_prf(gold, by_tau[OPERATING_TAU], lenient=True)
    at05 = metrics.entity_prf(gold, by_tau[0.5])
    per_genre = {}
    for g in sorted({r["genre"] for r in dev_eval_docs}):
        idx = [i for i, r in enumerate(dev_eval_docs) if r["genre"] == g]
        s = metrics.entity_prf([gold[i] for i in idx], [by_tau[OPERATING_TAU][i] for i in idx])
        per_genre[g] = {"docs": len(idx), "micro_recall": s["micro"]["recall"], "micro_precision": s["micro"]["precision"]}
    print(f"[{label}] tau={OPERATING_TAU}: micro P {strict['micro']['precision']} R {strict['micro']['recall']} F1 {strict['micro']['f1']} | "
          f"lenient R {lenient['micro']['recall']} | tau=0.5 R {at05['micro']['recall']}")
    for t in ENTITY_TYPES:
        if strict[t]["support"]:
            print(f"   {t:14s} P {strict[t]['precision']:.3f}  R {strict[t]['recall']:.3f}  F1 {strict[t]['f1']:.3f}  n={strict[t]['support']}")
    return {"strict_at_operating_tau": strict, "lenient_at_operating_tau": lenient, "strict_at_0.5": at05,
            "recall_sweep": metrics.recall_sweep(gold, by_tau), "per_genre": per_genre}


model.eval()


def torch_logits(x, m):
    with torch.no_grad():
        dev = next(model.parameters()).device
        return model(input_ids=torch.tensor(x).to(dev), attention_mask=torch.tensor(m).to(dev)).logits.float().cpu().numpy()


fp32_report = evaluate(torch_logits, "fp32 torch")

# %% Export to ONNX (optimum) and quantise to int8 (dynamic, per-channel)
from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: E402
from optimum.onnxruntime import ORTModelForTokenClassification  # noqa: E402

onnx_dir = WORK / "onnx_fp32"
ORTModelForTokenClassification.from_pretrained(str(best_dir), export=True).save_pretrained(str(onnx_dir))
fp32_path = onnx_dir / "model.onnx"
int8_path = OUT / "model.onnx"
quantize_dynamic(str(fp32_path), str(int8_path), weight_type=QuantType.QInt8, per_channel=True, reduce_range=False,
                 extra_options={"MatMulConstBOnly": True})
print(f"fp32 {fp32_path.stat().st_size / 1e6:.0f} MB -> int8 {int8_path.stat().st_size / 1e6:.0f} MB")

# %% Re-evaluate int8 and enforce the gate: int8 recall may not drop more than 1 point
import onnxruntime as ort  # noqa: E402


def make_session(path, threads):
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


sess8 = make_session(int8_path, 4)
in_names = {i.name for i in sess8.get_inputs()}


def onnx_logits(x, m):
    feed = {"input_ids": x.astype("int64"), "attention_mask": m.astype("int64")}
    if "token_type_ids" in in_names:
        feed["token_type_ids"] = np.zeros_like(x, dtype="int64")
    return sess8.run(None, feed)[0]


int8_report = evaluate(onnx_logits, "int8 onnx")


def recall_view(rep):
    s = rep["strict_at_operating_tau"]
    return {"micro": s["micro"]["recall"], "per_type": {t: (s[t]["recall"], s[t]["support"]) for t in ENTITY_TYPES}}


def recall_view05(rep):
    s = rep["strict_at_0.5"]
    return {"micro": s["micro"]["recall"], "per_type": {t: (s[t]["recall"], s[t]["support"]) for t in ENTITY_TYPES}}


drop = max(card.max_recall_drop(recall_view(fp32_report), recall_view(int8_report)),
           card.max_recall_drop(recall_view05(fp32_report), recall_view05(int8_report)))
print("max recall drop fp32 -> int8 (points):", round(drop * 100, 2))
assert drop <= 0.01, f"int8 recall dropped by {drop * 100:.2f} points (> 1): do not ship this quantisation; try reduce_range/other op types"

# %% CPU latency (ms per 512 tokens)
rng = np.random.default_rng(0)
ids512 = rng.integers(5, 1000, size=(1, 512)).astype("int64")
msk512 = np.ones_like(ids512)


def latency(path, threads, runs=25):
    s = make_session(path, threads)
    feed = {"input_ids": ids512, "attention_mask": msk512}
    if "token_type_ids" in {i.name for i in s.get_inputs()}:
        feed["token_type_ids"] = np.zeros_like(ids512)
    for _ in range(3):
        s.run(None, feed)
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        s.run(None, feed)
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {"median_ms": round(ts[len(ts) // 2], 1), "p95_ms": round(ts[int(len(ts) * 0.95) - 1], 1)}


lat = {"cpu": os.cpu_count(), **{f"int8_{n}thread": latency(int8_path, n) for n in (1, 4)}, **{f"fp32_{n}thread": latency(fp32_path, n) for n in (1, 4)}}
print(json.dumps(lat, indent=2))

# %% Write the artifact bundle: model.onnx, tokenizer, labels.json, metrics.json, MODEL_CARD.md, sha256sums
import shutil  # noqa: E402

tokenizer.save_pretrained(str(OUT))
shutil.copyfile(best_dir / "config.json", OUT / "config.json")
(OUT / "labels.json").write_text(json.dumps({"bio_labels": list(BIO_LABELS), "entity_types": list(ENTITY_TYPES), "max_len": MAX_LEN,
                                              "stride": STRIDE, "operating_tau": OPERATING_TAU, "base_model": BASE_MODEL}, indent=2))
metrics_json = {"training_data": "synthetic-only", "real_data_validated": False, "base_model": BASE_MODEL, "licence": LICENCE,
                "loss": LOSS, "seed": SEED, "train_docs": len(train_recs), "dev_docs": len(dev_recs), "templates": len(templates),
                "micro_recall": int8_report["strict_at_operating_tau"]["micro"]["recall"], "labels": list(ENTITY_TYPES),
                "operating_tau": OPERATING_TAU, "fp32": fp32_report, "int8": int8_report,
                "int8_vs_fp32": {"max_recall_drop": drop, "gate_points": 1.0}, "latency_ms_per_512_tokens": lat}
(OUT / "metrics.json").write_text(json.dumps(metrics_json, indent=2, default=str))
shutil.rmtree(OUT / "hf", ignore_errors=True)   # the fp32 torch weights are not part of the bundle
card.write_card(OUT / "MODEL_CARD.md", "PGW token classifier (PII and identifier spans)", "Qwen2.5-7B-Instruct writing templates",
                "Second-line detector in the HR privacy gateway: finds PERSON, EMAIL, PHONE, EMPLOYEE_ID, COMPENSATION, DATE_OF_BIRTH, "
                "AADHAAR, PAN, BANK_ACCOUNT, CARD, UPI_ID, SECRET, IFSC and IDENTIFIER spans to mask before text leaves the boundary.",
                BASE_MODEL, LICENCE, ENTITY_TYPES, {"micro_recall": metrics_json["micro_recall"], "recall_sweep": int8_report["recall_sweep"],
                                                    "int8_vs_fp32": metrics_json["int8_vs_fp32"], "latency_ms_per_512_tokens": lat})
sums = card.write_sha256sums(OUT)
print("model_sha256 (pin this):", sums["model.onnx"])
print(json.dumps(sums, indent=2))
print("\nDownload the 'token_classifier' folder from Output and run scripts/ml/import_artifacts.py on it.")
