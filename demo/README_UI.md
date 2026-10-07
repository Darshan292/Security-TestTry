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
- **Checks:** are protected and declared values absent from the outbound bytes? Did the egress check pass? Is every alias restored? Are C3 values never returned? Do refused requests send nothing?
- **What each side saw:** the app input, the exact bytes sent to the LLM (aliases highlighted; hover to see the original), the raw LLM response and the restored answer.
- **Alias map:** the original value behind each alias, shown on this machine only.

The private route uses an extractive stand-in, not an LLM, until a local GGUF model is configured (`PGW_PRIVATE_GGUF`).
