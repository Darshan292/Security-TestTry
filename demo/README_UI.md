# Privacy Gateway — Live Inspector (local test UI)

**Synthetic data only.** The inspector shows original values next to their aliases. It binds to `127.0.0.1` only; never expose it.

## Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/runtime.txt
pip install uvicorn==0.54.0
# optional, recommended (local NER safety net):
pip install --require-hashes -r requirements/ner.txt
pip install -r requirements/ner-model.txt

export GROQ_API_KEY=...            # real LLM; without it a clearly-labelled mock LLM is used
python -m demo.ui_server           # open http://127.0.0.1:8765
```

## What you see

- **Status bar:** the provider and model, the NER mode, the policy/contract integrity digest, the private-route backend, and warnings.
- **Scenarios:**
  - A1–A4: external LLM on protected text, with declared subjects.
  - B1–B3: local computation. Exact salaries and wages never leave.
  - C4: a Hindi ticket on the private, in-boundary route.
  - MT: multi-turn. Run it, then press **Continue conversation**.
  - FAIL: a fail-closed refusal.
- **Editing:** every field can be edited (purpose, messages, declared subjects JSON, conversation id).
- **Timeline:** the 13 steps stream live with timings:
  - request;
  - authentication;
  - data contract;
  - declared subjects;
  - route;
  - local computation;
  - detection and protection;
  - egress invariant;
  - exact bytes sent;
  - LLM response;
  - restoration and output gate;
  - audit row;
  - answer.

  Click a step to see its details.
- **Checks:** each shows ✓ (pass), ✗ (fail) or **N/A** (does not apply to this route or run).
  - The "values absent" check only covers values the gateway **detected or the app declared in this request**. It cannot show that undetected personal data did not leak.
  - On the private route the external-egress, declared-subject and alias checks are N/A. A separate check verifies that the stand-in's answer withholds the C3 values and long digit runs found in the input.
- **What each side saw:** the app input, the provider request, the raw provider response and the restored answer.
  - The provider request is **pretty-printed JSON**, not a byte-for-byte rendering of the HTTP exchange. Its caption gives the serialized body's size and SHA-256, which matches the audit row.
  - Aliases are highlighted; hover over one to see the original.
- **Alias map:** the original value behind each alias, shown on this machine only.

The private route uses an extractive stand-in, not an LLM, until a local GGUF model is configured (`PGW_PRIVATE_GGUF`).

## Local-request protections

The UI can spend the configured provider key, so it accepts only local, same-page requests:

- **Network binding:** it binds `127.0.0.1` only.
- **Host header:** it must be `127.0.0.1:<port>` or `localhost:<port>`; anything else gets 421. This blocks DNS-rebinding pages.
- **`POST /api/run` requirements:**
  - the exact same-origin `Origin` header;
  - an `X-PGW-UI: 1` header;
  - `Content-Type: application/json`.

  Any other origin triggers a CORS preflight, which the server refuses without CORS headers. Plain HTML forms cannot set the header or the JSON type.
- **Size and concurrency limits:** request bodies up to 64 KB and 20 messages; one run at a time (429 if busy). The gateway's own size, time and concurrency limits apply after that.
- **Response headers:** a strict CSP (`frame-ancestors 'none'`, `connect-src 'self'`), `X-Frame-Options: DENY`, `nosniff` and `no-referrer`.

None of this makes the inspector safe to expose on a network. It shows original values next to aliases by design.
