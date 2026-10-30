# Private LLM benchmark (llama.cpp llama-server, CPU)

Results of benchmarking one small open model on one CPU machine as the in-house endpoint for the PRIVATE route. See the caveat at the end before reading any number as a quality or capacity claim.

## Caveat

**One machine, synthetic prompts, not a quality evaluation.** All prompts were written for this test (no real or personal data). The numbers describe this 4-vCPU VM, this model file, this quantisation and these settings only. The quality notes are one reviewer's first impression of 3 runs per task type, not an evaluation against labels.

## Model

- Model: Qwen2.5-1.5B-Instruct, GGUF, quantisation Q4_K_M (`qwen2.5-1.5b-instruct-q4_k_m.gguf`, 1,117,320,736 bytes).
- Source: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF , revision `91cad51170dc346986eccefdc2dd33a9da36ead9`, file URL `resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf`.
- Licence (repo README.md at that revision): `license: apache-2.0` (licence link: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/main/LICENSE). Check the model's acceptable-use terms before any use.
- sha256: `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`
- The model file is kept outside the repository (`/home/user/models/qwen2.5-1.5b/`) and is not committed.

## Machine

- `nproc`: 4 (vCPU, one VM)
- CPU: Intel(R) Xeon(R) Processor @ 2.10GHz (from `/proc/cpuinfo`)
- RAM: 15 GiB total, no swap. Linux 6.18 (`uname`), Python 3.11.17 in a venv
- GPU: none. llama-cpp-python 0.3.36 built with `-DGGML_NATIVE=ON`, CPU only.

## Commands

```bash
python3 -m venv /home/user/venv-llm
CMAKE_ARGS="-DGGML_NATIVE=ON" /home/user/venv-llm/bin/pip install "llama-cpp-python[server]"   # built cleanly, no errors
/home/user/venv-llm/bin/pip install httpx                                                   # for scripts/bench_private_llm.py

# repo benchmark, concurrency 1 (the setting one Llama instance serves reliably)
/home/user/venv-llm/bin/python -m llama_cpp.server --model /home/user/models/qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --host 127.0.0.1 --port 8090 --n_ctx 4096 --n_threads 4
python scripts/bench_private_llm.py --url http://127.0.0.1:8090/v1 --model /home/user/models/qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --requests 20 --concurrency 1 --max-tokens 256 --stream --json bench_c1.json
# the same at --concurrency 2 failed for 19 of 20 requests (see below)
```

The task runs used a fresh server process per run (cold KV cache), a streamed `/v1/chat/completions` request with `temperature` 0, `max_tokens` 320 (email, summary) or 8 (classification), and `n_ctx` 4096, `n_threads` 4. Each run started its own `llama_cpp.server`, sent one request, read the process's peak RSS (`VmHWM` in `/proc/<pid>/status`) and stopped it.

## Results: repo benchmark (`scripts/bench_private_llm.py`, built-in four HR prompts, `--stream`, `--max-tokens 256`)

| run | requests ok / failed | latency p50 / p95 (ms) | TTFT p50 / p95 (ms) | tok/s per request (mean / p50) | aggregate tok/s | wall (s) |
|---|---|---|---|---|---|---|
| concurrency 1 | 20 / 0 | 6425.5 / 33343.2 | 704.4 / 901.7 | 7.35 / 7.0 | 6.99 | 233.55 |
| concurrency 2 | 1 / 19 | 6068.1 / 6068.1 (only 1 ok) | 1125.3 / 1125.3 | 8.9 / 8.9 | 2.75 | 15.98 |

Concurrency 2 failed with `RemoteProtocolError` on 19 of 20 requests. The server log shows that the clients disconnected while the single Llama instance was serving another streamed request. So with llama-cpp-python's server on this setup, run one request at a time (or use a server that supports parallel slots, which is a separate test). The concurrency-1 run is the usable figure. The script reuses four prompts, so the llama.cpp prompt-prefix cache helped many of its requests; treat its prompt-processing figures as optimistic.

## Results: nine synthetic tasks (3 runs each, cold, one fresh server per run)

Prompt processing speed is derived as prompt tokens / time to first token (TTFT includes the first generated token, so this slightly understates it). The llama.cpp per-request timing lines in the server log were not usable (they reported about 0.3 ms for 349 prompt tokens), so they are not quoted. Generation tok/s = (completion tokens - 1) / (total time - TTFT), counted client-side from streamed chunks. The classification answer is 2 tokens, so no meaningful generation rate.

| task | run | prompt tokens | TTFT (s) | prompt tok/s (derived) | gen tok/s | total latency (s) | output tokens | peak RSS (MiB) |
|---|---|---|---|---|---|---|---|---|
| a_summary | 1 | 349 | 8.76 | 39.8 | 4.41 | 39.4 | 136 | 2757 |
| a_summary | 2 | 349 | 13.20 | 26.4 | 4.53 | 43.0 | 136 | 2757 |
| a_summary | 3 | 349 | 7.89 | 44.2 | 4.81 | 36.0 | 136 | 2758 |
| b_email | 1 | 104 | 2.97 | 35.1 | 4.32 | 32.8 | 130 | 2322 |
| b_email | 2 | 104 | 2.98 | 34.8 | 4.59 | 31.1 | 130 | 2321 |
| b_email | 3 | 104 | 3.32 | 31.3 | 4.44 | 32.4 | 130 | 2321 |
| c_classify | 1 | 372 | 8.37 | 44.5 | n/a (2 tokens) | 8.8 | 2 | 2721 |
| c_classify | 2 | 372 | 7.65 | 48.7 | n/a (2 tokens) | 8.0 | 2 | 2721 |
| c_classify | 3 | 372 | 8.66 | 43.0 | n/a (2 tokens) | 9.2 | 2 | 2721 |

Mean across the three runs per task:

| task | mean TTFT (s) | mean total latency (s) |
|---|---|---|
| (a) Summarise a helpdesk ticket in 3 bullets | 9.95 | 39.4 |
| (b) Draft a 120-word reply email | 3.09 | 32.1 |
| (c) Classify into one of 6 categories (one word) | 8.22 | 8.7 |

**Peak RSS of the server process: 2758 MiB** (max over the nine runs; the 1.1 GB GGUF is memory-mapped and counted in RSS once paged in). The machine has 15 GiB.

## Outputs, verbatim (temperature 0, one run each)

Runs 2 and 3 of each task produced output identical to run 1 (checked by string comparison).

### (a) Summarise a helpdesk ticket in 3 bullets

Runs 1-3 identical: True

```text
- Employee's laptop cannot connect to office Wi-Fi network after Saturday update, appears in list but connection fails with invalid certificate.
- Laptop restarted twice and profile removed/re-added without success.
- Colleagues can connect using same password, suggesting issue is on device.
- A client review call at 2 PM requires stable video software connection.
- Phone's hotspot works but slow and runs out of data quickly.
- Guest network works but does not reach internal shared drive with files needed for the call.
- New update may have changed security settings or enrollment status in device management.
- Available until 5 PM, can bring laptop to IT service desk if remote help is not possible.
```

**Quality note:** Not followed: asked for 3 bullets, got 8 (one per ticket sentence). The content is faithful to the ticket, with no invented facts; the "works" for the hotspot is a small paraphrase.

### (b) Draft a 120-word reply email

Runs 1-3 identical: True

```text
Subject: Information on Unused Casual Leave Days Carrying Over to Next Year

Dear Employee ID 5020,

Thank you for your inquiry regarding the policy on unused casual leave days carrying over to next year. According to our HR policies, up to five unused casual leave days carry over each year.

Please note that balances of unused leave are visible under "Leave" in the self-service portal. This feature allows employees to track their accumulated leave time and understand how many days they can still use for future periods.

If you have any further questions or need clarification on this policy, please do not hesitate to reach out.

Best regards,
HR Helpdesk
```

**Quality note:** Mostly followed: about 110 words, correct sign-off, and the policy facts are the ones given in the prompt, with no invented numbers. The middle paragraph is filler a human would cut.

### (c) Classify into one of 6 categories (one word)

Runs 1-3 identical: True

```text
IT Access
```

**Quality note:** Plausible: "IT Access" fits a network-connectivity ticket, and the answer is one word as asked. It is one label from one run, so it says nothing about accuracy on a labelled set (Other or Payroll would not be wrong here either).

## Reading these numbers

- Latency is dominated by generation at about 4.5 tok/s, so a 130-token answer takes about 30 s on this VM. Size `upstream_timeout_s` from measured p95 values, not from these means.
- A single request at a time is the only setting measured as working here.
- None of this says whether the model is good enough for HR content. That needs a labelled evaluation on real, approved data inside the boundary, and a review of the gateway's behaviour with this model.

