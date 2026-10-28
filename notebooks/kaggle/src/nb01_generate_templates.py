# %% [markdown]
# # 01 - Generate document templates with an open LLM
# Qwen2.5-7B-Instruct (Apache-2.0; alternate Mistral-7B-Instruct-v0.3) writes **templates** with typed slots such as
# `{{PERSON_1}}`, `{{AADHAAR_1}}`. Python (not the LLM) fills the slots and computes the labels in notebook 02.
# Settings: GPU **T4 x2**, Internet **on**. Resumable: re-run the notebook and finished jobs are skipped.
# Output: `templates.jsonl` (token classifier) and `gate_templates.jsonl` (content gate) in the working directory.

# %% include _common_env.py

# %% Configuration
SEED = 1234
GENERATOR = os.environ.get("PGW_GENERATOR", "Qwen/Qwen2.5-7B-Instruct")   # alternate: mistralai/Mistral-7B-Instruct-v0.3
N_TEMPLATE_JOBS = int(os.environ.get("PGW_N_TEMPLATE_JOBS", "2400"))      # x3 templates per call, ~60-70% survive validation
N_GATE_JOBS = int(os.environ.get("PGW_N_GATE_JOBS", "900"))               # x4 texts per call
BATCH = int(os.environ.get("PGW_BATCH", "32"))                             # prompts per generate call (vLLM); HF 4-bit uses 8
MAX_HOURS = float(os.environ.get("PGW_MAX_HOURS", "10.5"))                 # stop cleanly before the 12 h Kaggle limit
ALLOW_OFFLINE_LICENCE_TABLE = os.environ.get("PGW_OFFLINE_LICENCE", "0") == "1"
OUT = WORK / "templates"
OUT.mkdir(exist_ok=True)
T_PATH, G_PATH, REJ_PATH, STATE_PATH = OUT / "templates.jsonl", OUT / "gate_templates.jsonl", OUT / "rejects.jsonl", OUT / "state.json"
T0 = time.time()

# %% Licence check (prints the model card licence, asserts it is allowed)
pip_install("huggingface_hub")
from ml.train import licences  # noqa: E402

LICENCE = licences.check_model(GENERATOR, licences.hub_licence, allow_table_fallback=ALLOW_OFFLINE_LICENCE_TABLE)

# %% Pick a backend: vLLM fp16 tensor-parallel on 2 x T4, else transformers 4-bit
import torch  # noqa: E402

N_GPU = torch.cuda.device_count()
CAP = torch.cuda.get_device_capability(0) if N_GPU else (0, 0)
print("GPUs:", N_GPU, [torch.cuda.get_device_name(i) for i in range(N_GPU)], "capability", CAP)
assert N_GPU >= 1, "enable a GPU (Kaggle: Settings > Accelerator > GPU T4 x2)"
BACKEND = "hf4bit"
if N_GPU >= 2 and CAP >= (7, 0) and os.environ.get("PGW_FORCE_HF", "0") != "1":
    try:
        pip_install("vllm==0.6.3.post1")
        import vllm  # noqa: F401
        BACKEND = "vllm"
    except Exception as e:  # noqa: BLE001
        print("vLLM unavailable, falling back to transformers 4-bit:", type(e).__name__, e)
if BACKEND == "hf4bit":
    pip_install("bitsandbytes>=0.43", "accelerate", "transformers")
print("backend:", BACKEND)

# %% Load the generator
if BACKEND == "vllm":
    os.environ.setdefault("VLLM_ATTENTION_BACKEND", "XFORMERS")  # T4 has no flash-attention
    from vllm import LLM, SamplingParams
    llm = LLM(GENERATOR, dtype="float16", tensor_parallel_size=N_GPU, max_model_len=6144, gpu_memory_utilization=0.90, enforce_eager=True)
    tok = llm.get_tokenizer()
else:
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(GENERATOR, padding_side="left")
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        GENERATOR, device_map="auto", torch_dtype=torch.float16,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16))
    model.eval()


def generate(jobs_batch):
    """-> list of reply strings, one per job (each job carries its own messages, temperature and token budget)."""
    prompts = [tok.apply_chat_template(j["messages"], tokenize=False, add_generation_prompt=True) for j in jobs_batch]
    if BACKEND == "vllm":
        params = [SamplingParams(temperature=j["controls"]["temperature"], top_p=0.95, max_tokens=j["max_new_tokens"], seed=SEED + j["job"])
                  for j in jobs_batch]
        return [o.outputs[0].text for o in llm.generate(prompts, params, use_tqdm=False)]
    enc = tok(prompts, return_tensors="pt", padding=True).to(model.device)
    with torch.no_grad():
        out = model.generate(**enc, do_sample=True, temperature=jobs_batch[0]["controls"]["temperature"], top_p=0.95,
                             max_new_tokens=max(j["max_new_tokens"] for j in jobs_batch), pad_token_id=tok.pad_token_id)
    return [tok.decode(o[enc["input_ids"].shape[1]:], skip_special_tokens=True) for o in out]


# %% Checkpoint / resume state
from ml.train import io as mio, jobs  # noqa: E402

state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {"done_template_jobs": [], "done_gate_jobs": [], "stats": {}}
done = {"template": set(state["done_template_jobs"]), "gate": set(state["done_gate_jobs"])}
print("resuming with", {k: len(v) for k, v in done.items()}, "jobs already finished")


def save_state(stats):
    state["done_template_jobs"], state["done_gate_jobs"], state["stats"] = sorted(done["template"]), sorted(done["gate"]), stats
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_PATH)


# %% Budget estimate (printed before the long run, refined after the first batch)
TOK_PER_CALL = 1100
n_todo = (N_TEMPLATE_JOBS - len(done["template"])) + (N_GATE_JOBS - len(done["gate"]))
TPS = {"vllm": 900.0, "hf4bit": 120.0}[BACKEND]   # conservative aggregate tokens/s on T4 x2 (vLLM) or one T4 (4-bit); measured below
print(f"jobs to run: {n_todo}; ~{TOK_PER_CALL} tokens each; first estimate {n_todo * TOK_PER_CALL / TPS / 3600:.1f} GPU-hours at {TPS:.0f} tok/s "
      f"(weekly Kaggle quota is 30 h; this session stops at {MAX_HOURS} h)")

# %% Generate (templates, then gate texts)
import collections  # noqa: E402

stats = collections.Counter(state.get("stats", {}))
step = BATCH if BACKEND == "vllm" else 8
tokens_seen, t_gen = 0, time.time()
detector = None   # the stronger repo-detector filter runs locally afterwards: scripts/ml/validate_templates.py

for kind, n_jobs, make, path in (("template", N_TEMPLATE_JOBS, lambda i: jobs.template_job(SEED, i), T_PATH),
                                 ("gate", N_GATE_JOBS, lambda i: jobs.gate_job(SEED, i), G_PATH)):
    todo = [i for i in range(n_jobs) if i not in done[kind]]
    for b in range(0, len(todo), step):
        if (time.time() - T0) / 3600 > MAX_HOURS:
            print("time budget reached; stopping cleanly. Re-run to continue.")
            break
        batch = [make(i) for i in todo[b:b + step]]
        replies = generate(batch)
        acc, rej = [], []
        for job, raw in zip(batch, replies, strict=True):
            ok, why = jobs.accept(job, raw, GENERATOR, detector)
            acc += ok
            rej += [{"job": job["job"], "kind": kind, "reason": r} for r in why]
            tokens_seen += len(raw) // 4
            done[kind].add(job["job"])
        mio.write_jsonl(path, acc, append=True)
        if rej:
            mio.write_jsonl(REJ_PATH, rej, append=True)
        stats[f"{kind}_accepted"] += len(acc)
        stats[f"{kind}_rejected"] += len(rej)
        save_state(dict(stats))
        if (b // step) % 5 == 0:
            tps = tokens_seen / max(time.time() - t_gen, 1)
            left = n_todo - sum(len(v) for v in done.values())
            print(f"{kind}: {len(done[kind])}/{n_jobs} jobs | accepted {stats[kind + '_accepted']} rejected {stats[kind + '_rejected']} | "
                  f"~{tps:.0f} tok/s | est. {left * TOK_PER_CALL / max(tps, 1) / 3600:.1f} h left")

# %% Summary, reject reasons, manifest
print(dict(stats))
rej_counts = collections.Counter(r["reason"] for r in mio.read_jsonl(REJ_PATH)) if REJ_PATH.exists() else {}
print("top reject reasons:", rej_counts.most_common(8))
by_genre = collections.Counter(t["genre"] for t in mio.read_jsonl(T_PATH)) if T_PATH.exists() else {}
print("templates per genre:", dict(by_genre))
manifest = {"generator": GENERATOR, "licence": LICENCE, "backend": BACKEND, "seed": SEED, "stats": dict(stats),
            "files": {p.name: mio.sha256_file(p) for p in (T_PATH, G_PATH) if p.exists()}}
(OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
print("\nNEXT: download the 'templates' folder from Output, run scripts/ml/validate_templates.py and "
      "scripts/ml/contamination_check.py LOCALLY, then upload the cleaned files as a Kaggle dataset for notebooks 02 and 03.")
