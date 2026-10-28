# In-house CPU LLM for the PRIVATE route: llama.cpp `llama-server`

The PRIVATE route (C4 content, unattested input, special-category content caught by the content gate) sends the
caller's **unprotected** text to a model inside the boundary. The gateway already supports any OpenAI-compatible
endpoint there (`private_upstream_url` in the signed profile). This page wires llama.cpp's `llama-server`
(MIT licence, CPU, GGUF weights) as that endpoint. No model is downloaded or shipped by this repository.

## 1. Pick and verify a model (outside this repository)

- Choose an instruction-tuned model under a commercial-friendly licence (e.g. Apache-2.0 or MIT; read the model's
  own licence and acceptable-use terms) in GGUF format, at a quantisation your CPUs can serve (Q4_K_M or Q5_K_M are
  common starting points for 7-8B models; measure, section 4).
- Record its source URL, licence, file name and `sha256sum` in your deployment record. Keep the file on the model
  host only; mount it read-only (the compose stub does).

## 2. Run it (compose stub)

`deploy/docker-compose.yml` has a service `pgw-private-llm`, off by default (compose profile `private-llm`):

- image pinned by tag **and digest**: `ghcr.io/ggml-org/llama.cpp:server-b11723@sha256:1439260…3a5a07` (multi-arch
  index; bump both together after testing);
- model directory mounted read-only at `/models` (`PGW_PRIVATE_GGUF_DIR`, `PGW_PRIVATE_GGUF_FILE`);
- `--alias ${PGW_PRIVATE_MODEL_ALIAS}`: the id `GET /v1/models` reports. Set the gateway's `PGW_PRIVATE_MODEL` to the
  same value;
- CPU: `--threads`/`--threads-batch` (`LLAMA_THREADS`, physical cores for this container), `cpus` limit
  (`LLAMA_CPUS`), `--ctx-size` (`LLAMA_CTX`) shared by `--parallel` slots (`LLAMA_PARALLEL`);
- on `privatenet` only (`internal: true`, no route out); only the gateway is also attached to it. It is not on
  `appnet`, so no app can call it directly, and not on `egressnet`.

```bash
PGW_PRIVATE_GGUF_DIR=/srv/models PGW_PRIVATE_GGUF_FILE=model-q4_k_m.gguf PGW_PRIVATE_MODEL_ALIAS=hr-private-llm \
LLAMA_THREADS=8 LLAMA_CPUS=8 docker compose -f deploy/docker-compose.yml --profile private-llm up -d pgw-private-llm
```

Then, in a reviewed and re-signed change of `registry/deployment.yaml`:

```yaml
  private_upstream_url: http://pgw-private-llm:8080/v1      # host already in the default private_allowlist
```

and in the gateway environment `PGW_PRIVATE_MODEL=hr-private-llm`.

Kubernetes: the same container, a NetworkPolicy that admits only the gateway pods, a read-only volume for the model,
CPU requests = limits = the thread count.

## 3. What the gateway checks

- At startup it calls `GET /v1/models`. llama-server answers with an OpenAI model list (`data[].id` = the alias,
  `owned_by: llamacpp`, plus `meta` and an Ollama-style `models` list), which the gateway accepts as a real model
  (`tests/test_ml_gateway.py::test_llama_server_identity_is_accepted_and_the_standin_still_refused`).
- The development stand-in (`pgw.private_llm` without weights) still identifies itself as `extractive-standin`;
  production refuses it at startup, also when it hides among other listed models, and withholds any later reply that
  carries its marker.
- The destination must be a host in the signed `private_allowlist` and never an external provider host.
- llama-server's default logging does not write prompts; do not raise its verbosity in production, and keep its
  container logs on the same retention rules as the gateway's.

## 4. Benchmark on your hardware

```bash
python scripts/bench_private_llm.py --url http://pgw-private-llm:8080/v1 --model hr-private-llm \
    --requests 20 --concurrency 2 --max-tokens 256 --stream --json bench.json
```

It reports latency p50/p95, time to first token, tokens/s per request and aggregate throughput, and counts failures.
Run it from inside the boundary (it ignores proxy settings). Record the CPU model, cores, model file, quantisation,
`--threads`, `--ctx-size` and `--parallel` with the result: the numbers mean nothing without them. Size
`upstream_timeout_s` (the private route waits for the whole answer) from the measured p95 at the concurrency you
expect.
