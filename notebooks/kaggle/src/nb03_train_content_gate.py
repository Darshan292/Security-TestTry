# %% [markdown]
# # 03 - Train the content gate (multi-label sentence/document classifier)
# Labels: HEALTH, DISABILITY, PREGNANCY, SEXUAL_ORIENTATION, RELIGION, CASTE, POLITICAL, UNION_MEMBERSHIP, BIOMETRIC,
# CRIMINAL_OR_DISCIPLINARY, PERFORMANCE_RATING, FINANCIAL_HARDSHIP, MINOR, NONE. Base: microsoft/Multilingual-MiniLM-L12-H384 (MIT;
# alternate FacebookAI/xlm-roberta-base). A per-label threshold is chosen on the dev split for recall >= 0.95 (precision is reported).
# Settings: GPU T4 (one is enough), Internet **on**. Inputs: the `ml/train` dataset and the cleaned templates dataset
# (gate_templates.jsonl; optional - programmatic seed sentences are used regardless).

# %% include _common_env.py

# %% Configuration
SEED = 1234
BASE_MODEL = os.environ.get("PGW_GATE_BASE", "microsoft/Multilingual-MiniLM-L12-H384")   # alternate: FacebookAI/xlm-roberta-base
N_DOCS = int(os.environ.get("PGW_GATE_DOCS", "30000"))
PROGRAMMATIC_FRACTION = 0.4
DEV_TEMPLATE_FRACTION = 0.1
MAX_LEN = 256
EPOCHS, LR, BATCH = 5, 6e-5, 32
RECALL_TARGET = 0.95
ALLOW_OFFLINE_LICENCE_TABLE = os.environ.get("PGW_OFFLINE_LICENCE", "0") == "1"
OUT, DATA, CKPT = WORK / "content_gate", WORK / "gate_data", WORK / "gate_ckpt"
for d in (OUT, DATA, CKPT):
    d.mkdir(exist_ok=True)

# %% Install and licence check
pip_install("transformers>=4.41", "accelerate", "optimum[onnxruntime]>=1.20", "onnx", "onnxruntime", "huggingface_hub", "sentencepiece")
from ml.train import licences  # noqa: E402

LICENCE = licences.check_model(BASE_MODEL, licences.hub_licence, allow_table_fallback=ALLOW_OFFLINE_LICENCE_TABLE)

# %% Build the data (split BY TEMPLATE; programmatic documents split by seed)
import collections  # noqa: E402
import math  # noqa: E402
import random as _r  # noqa: E402

from ml.train import gate, io as mio, synth  # noqa: E402
from ml.train.labels import GATE_LABELS, GATE_TO_PURPOSE_DEPENDENT  # noqa: E402

files = find_files("gate_templates.jsonl")
templates = list(mio.read_jsonl(files[0])) if files else []
print("gate templates:", len(templates))
train_ids, dev_ids = synth.split_templates([t["id"] for t in templates], DEV_TEMPLATE_FRACTION, SEED) if templates else (set(), set())
assert not (train_ids & dev_ids)
n_prog = int(N_DOCS * PROGRAMMATIC_FRACTION) if templates else N_DOCS
tt = [t for t in templates if t["id"] in train_ids]
dt = [t for t in templates if t["id"] in dev_ids]
per = max(1, math.ceil((N_DOCS - n_prog) / max(len(tt), 1))) if tt else 0
train = (gate.gate_records_from_templates(tt, per, SEED)[: N_DOCS - n_prog] if tt else []) + gate.programmatic_gate_records(n_prog, SEED + 2)
dev = (gate.gate_records_from_templates(dt, 3, SEED + 1) if dt else []) + gate.programmatic_gate_records(max(600, n_prog // 12), SEED + 3)
_r.Random(SEED).shuffle(train)
mio.write_jsonl(DATA / "train.jsonl", train)
mio.write_jsonl(DATA / "dev.jsonl", dev)
print(len(train), "train |", len(dev), "dev |", dict(collections.Counter(l for r in train for l in r["labels"])))
L2I = {l: i for i, l in enumerate(GATE_LABELS)}


def onehot(r):
    v = [0.0] * len(GATE_LABELS)
    for l in r["labels"]:
        v[L2I[l]] = 1.0
    return v


# %% Tokenizer and model (MiniLM ships an XLM-R sentencepiece tokenizer with a BERT config)
import numpy as np  # noqa: E402
import torch  # noqa: E402
from transformers import (AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments, XLMRobertaTokenizer)  # noqa: E402

if "MiniLM" in BASE_MODEL:
    tokenizer = XLMRobertaTokenizer.from_pretrained(BASE_MODEL)
else:
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL, num_labels=len(GATE_LABELS), problem_type="multi_label_classification",
                                                           id2label=dict(enumerate(GATE_LABELS)), label2id=L2I)


class DS(torch.utils.data.Dataset):
    def __init__(self, recs):
        self.enc = tokenizer([r["text"] for r in recs], truncation=True, max_length=MAX_LEN)
        self.y = [onehot(r) for r in recs]

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        d = {k: v[i] for k, v in self.enc.items() if k in ("input_ids", "attention_mask")}
        d["labels"] = self.y[i]
        return d


def collate(batch):
    width = max(len(b["input_ids"]) for b in batch)
    ids = torch.full((len(batch), width), tokenizer.pad_token_id, dtype=torch.long)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    for r, b in enumerate(batch):
        ids[r, :len(b["input_ids"])] = torch.tensor(b["input_ids"])
        mask[r, :len(b["input_ids"])] = 1
    return {"input_ids": ids, "attention_mask": mask, "labels": torch.tensor([b["labels"] for b in batch], dtype=torch.float)}


def compute_metrics(p):
    probs = 1 / (1 + np.exp(-p.predictions))
    y = p.label_ids
    pred = probs >= 0.5
    tp = (pred & (y == 1)).sum(0)
    rec = tp / np.maximum((y == 1).sum(0), 1)
    return {"macro_recall": float(rec.mean()), "micro_f1": float(2 * tp.sum() / max(pred.sum() + (y == 1).sum(), 1))}


train_ds, dev_ds = DS(train), DS(dev)
args = TrainingArguments(output_dir=str(CKPT), num_train_epochs=EPOCHS, learning_rate=LR, warmup_ratio=0.06, weight_decay=0.01,
                         per_device_train_batch_size=BATCH, per_device_eval_batch_size=BATCH * 2, fp16=torch.cuda.is_available(),
                         eval_strategy="epoch", save_strategy="epoch", save_total_limit=2, load_best_model_at_end=True,
                         metric_for_best_model="micro_f1", greater_is_better=True, report_to="none", seed=SEED, remove_unused_columns=False)
trainer = Trainer(model=model, args=args, train_dataset=train_ds, eval_dataset=dev_ds, data_collator=collate, compute_metrics=compute_metrics)

# %% Train (resumable from WORK/gate_ckpt or from a previous run attached as input)
resume = None
for root in (CKPT, INPUT):
    found = sorted(root.rglob("checkpoint-*/trainer_state.json"), key=lambda p: int(p.parent.name.split("-")[1]))
    if found:
        resume = str(found[-1].parent)
        break
print("resuming from", resume)
trainer.train(resume_from_checkpoint=resume)
best = OUT / "hf"
trainer.save_model(str(best))
tokenizer.save_pretrained(str(best))
model.eval()

# %% Dev probabilities from fp32, per-label thresholds for recall >= 0.95
from ml.train import card, metrics  # noqa: E402


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def probs_with(logits_fn, recs, batch=64):
    out = []
    for b in range(0, len(recs), batch):
        chunk = recs[b:b + batch]
        enc = tokenizer([r["text"] for r in chunk], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="np")
        out.append(sigmoid(logits_fn(enc["input_ids"].astype("int64"), enc["attention_mask"].astype("int64"))))
    return np.concatenate(out)


def torch_logits(x, m):
    with torch.no_grad():
        dev_ = next(model.parameters()).device
        return model(input_ids=torch.tensor(x).to(dev_), attention_mask=torch.tensor(m).to(dev_)).logits.float().cpu().numpy()


y_dev = [onehot(r) for r in dev]
p32 = probs_with(torch_logits, dev)
report32 = metrics.multilabel_report(y_dev, p32.tolist(), GATE_LABELS, RECALL_TARGET)
thresholds = {l: v["threshold"] for l, v in report32.items()}
for l, v in report32.items():
    print(f"{l:26s} thr {v['threshold']:.3f}  recall {v.get('recall')}  precision {v.get('precision')}  n={v['support']}")
low = [l for l, v in report32.items() if v.get("recall_target_met") is False]
assert not low, f"recall target {RECALL_TARGET} not reachable for {low}"


def recalls_at(probs, thr):
    y = np.array(y_dev)
    out = {}
    for j, l in enumerate(GATE_LABELS):
        pos = y[:, j] == 1
        out[l] = (float((probs[pos, j] >= thr[l]).mean()) if pos.any() else 1.0, int(pos.sum()))
    return out


# %% ONNX export, int8 quantisation, and the int8-vs-fp32 recall gate (max 1 point per label with >= 30 positives)
from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: E402
from optimum.onnxruntime import ORTModelForSequenceClassification  # noqa: E402

onnx_dir = WORK / "gate_onnx_fp32"
ORTModelForSequenceClassification.from_pretrained(str(best), export=True).save_pretrained(str(onnx_dir))
fp32_path, int8_path = onnx_dir / "model.onnx", OUT / "model.onnx"
quantize_dynamic(str(fp32_path), str(int8_path), weight_type=QuantType.QInt8, per_channel=True, reduce_range=False,
                 extra_options={"MatMulConstBOnly": True})
import onnxruntime as ort  # noqa: E402


def session(path, threads):
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


s8 = session(int8_path, 4)
names = {i.name for i in s8.get_inputs()}


def onnx_logits(x, m):
    feed = {"input_ids": x, "attention_mask": m}
    if "token_type_ids" in names:
        feed["token_type_ids"] = np.zeros_like(x)
    return s8.run(None, feed)[0]


p8 = probs_with(onnx_logits, dev)
r32, r8 = recalls_at(p32, thresholds), recalls_at(p8, thresholds)
drop = max(card.max_recall_drop({"micro": 1.0, "per_type": r32}, {"micro": 1.0, "per_type": r8}), 0.0)
print("max per-label recall drop fp32 -> int8 (points):", round(drop * 100, 2))
assert drop <= 0.01, f"int8 recall dropped by {drop * 100:.2f} points (> 1)"
report8 = metrics.multilabel_report(y_dev, p8.tolist(), GATE_LABELS, RECALL_TARGET)

# %% CPU latency for 128 and 256 tokens, 1 and 4 threads
rng = np.random.default_rng(0)


def latency(path, threads, n_tok, runs=30):
    s = session(path, threads)
    x = rng.integers(5, 1000, size=(1, n_tok)).astype("int64")
    feed = {"input_ids": x, "attention_mask": np.ones_like(x)}
    if "token_type_ids" in {i.name for i in s.get_inputs()}:
        feed["token_type_ids"] = np.zeros_like(x)
    for _ in range(3):
        s.run(None, feed)
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        s.run(None, feed)
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {"median_ms": round(ts[len(ts) // 2], 1), "p95_ms": round(ts[int(len(ts) * 0.95) - 1], 1)}


lat = {"cpu": os.cpu_count(), **{f"int8_{n}thread_{t}tok": latency(int8_path, n, t) for n in (1, 4) for t in (128, 256)}}
print(json.dumps(lat, indent=2))

# %% Write the bundle
import shutil  # noqa: E402

# Runtime bundle contract (pgw/ml/artifact.py): flat dir with model.onnx (int8, weights inline), tokenizer.json, labels.json
# ("pgw-ml-labels/1"), MODEL_CARD.md. Labels come from the TRAINED model's config id2label, in index order.
tokenizer.save_pretrained(str(OUT))
shutil.rmtree(best, ignore_errors=True)     # fp32 torch weights are not part of the bundle
assert not [p.name for p in OUT.iterdir() if p.name.endswith((".onnx_data", ".onnx.data"))], "external ONNX data is not supported"
(OUT / "thresholds.json").write_text(json.dumps(thresholds, indent=2))
card.write_labels(OUT / "labels.json", card.sequence_labels_doc(model.config.id2label, tokenizer, MAX_LEN, stride=64, problem_type="multi_label"))
card.write_train_meta(OUT / "train_meta.json", {"recall_target": RECALL_TARGET, "base_model": BASE_MODEL, "max_len": MAX_LEN, "activation": "sigmoid",
                                                "purpose_dependent_mapping": GATE_TO_PURPOSE_DEPENDENT, "thresholds": thresholds})
mj = {"training_data": "synthetic-only", "real_data_validated": False, "base_model": BASE_MODEL, "licence": LICENCE, "seed": SEED,
      "train_docs": len(train), "dev_docs": len(dev), "labels": list(GATE_LABELS), "fp32_report": report32, "int8_report": report8,
      "int8_vs_fp32": {"max_recall_drop": drop, "gate_points": 1.0}, "latency_ms": lat}
(OUT / "metrics.json").write_text(json.dumps(mj, indent=2, default=str))
card.write_card(OUT / "MODEL_CARD.md", "PGW content gate (special-category and purpose-dependent content)", "Qwen2.5-7B-Instruct writing templates",
                "Flags text that discloses health, disability, pregnancy, orientation, religion, caste, political, union, biometric, "
                "disciplinary, performance-rating, hardship or minor information about a specific person, so the gateway can route or refuse it.",
                BASE_MODEL, LICENCE, GATE_LABELS, {"thresholds": thresholds, "int8": report8, "int8_vs_fp32": mj["int8_vs_fp32"], "latency_ms": lat})
card.check_bundle_dir(OUT)     # flat, one model.onnx, no external data / torch weights, tokenizer.json present
sums = card.write_sha256sums(OUT)
print("file hashes:", json.dumps(sums, indent=2))
print("The profile pin is the runtime manifest digest: run scripts/ml/import_artifacts.py (needs the pgw package) on the downloaded folder.")
print("\nDownload the 'content_gate' folder from Output and run scripts/ml/import_artifacts.py on it.")
