# ADR-0008: Streaming restore and the output gate

- **Status:** Proposed (Phase 0)
- **Date:** 2026-09-30
- **Supersedes:** none

## Context

Every surveyed product fails here in one of two ways:

- **Buffer everything** (Cloudflare with response scanning; the reported LiteLLM
  fix for split-chunk leaks, issue #41611 — cited by one research pass, not
  independently confirmed by the second): safe, but time-to-first-token = full generation time. Chat UX dies.
- **Pass through** (AWS Guardrails async mode, which *cannot mask*; LiteLLM
  streaming paths that repeatedly needed patches — PRs #42351, #43345, issue #42476): fast, leaky.

Tokenisers routinely split a 12-character placeholder across 2–3 deltas, so
per-chunk replacement never works.

## Decision

**Incremental restore with a bounded hold-back buffer; the output gate runs on
the *aliased* text before restoration; restoration is the last step.**

Order of operations per stream (per choice index, per content block):

```
provider SSE ─► parse deltas ─► [A] output gate on aliased text ─► [B] restorer ─► re-emit SSE
                                       │                               │
                          fabricated alias? raw PII?        hold back ≤ L_max chars that
                          policy violation?                 could be an alias prefix
```

**[B] Restorer** (the algorithm):

1. Append delta to buffer `b`.
2. Replace every complete alias in `b` that resolves in scope (strict form, then
   lenient forms: missing brackets, case changes — safe because of the 30-bit
   suffix, ADR-0002).
3. Find the longest suffix of `b` that is a proper prefix of the alias grammar
   `\[?[A-Z_]{2,20}_[0-9A-Z]{0,8}\]?` (case-insensitive). Hold it back; emit the rest.
4. `L_max` = 31 chars. Worst-case added latency = time to generate ~8 model
   tokens, only when the text actually looks like an alias.
5. On stream end: flush; unresolved alias-shaped text → gate event (below).

Tool-call argument deltas (`tool_calls[].function.arguments`) are buffered
**whole per call** (they are JSON; partial JSON is not useful to the client
anyway), gated, restored, emitted. This fixes the class of bug in LiteLLM #31950.

**[A] Output gate** (on aliased text, before restore — so restored values, which
are authorised by construction, are never the thing being judged):

| Check | Streaming mode | Action |
|---|---|---|
| Alias-shaped string not in scope (fabrication) | inline | Replace with `[unknown TYPE]`, event |
| D2 deterministic recognisers (Aadhaar, PAN, cards, secrets) | inline, sliding window 256 chars | Mask + event; policy may terminate stream |
| Echo of an input value the pipeline chose to ALLOW | n/a | Allowed |
| Raw value matching the tenant EDM dictionary (model "knew" a name) | inline (Aho-Corasick, cheap) | Mask + event |
| NER on output | **post-hoc**, non-blocking, for audit metrics only | Event. Non-streaming requests: blocking. |
| Restore policy (`never_restore: [SECRET]`, app `restore_output: false`) | inline | Leave alias |

Honest limit, stated in the product: **in streaming mode, output NER is an
audit control, not a prevention control.** Apps that need prevention use
non-streaming or `X-PGW-Stream-Mode: buffered`.

## Consequences

**Good.** Near-native TTFT; split-placeholder leaks impossible by construction;
tool arguments handled correctly; fabricated references caught.

**Bad.** Per-provider SSE parsing (OpenAI chat, OpenAI Responses, Anthropic
Messages) is ours to maintain; each gets a golden-stream test suite with
adversarial chunk boundaries (every alias split at every offset).

## Amendment R2 (2026-09-30) — the R1 claims were false; RESTRICTED output is buffered

**Withdrawn claims.** "Split-placeholder leaks impossible by construction" was false for whitespace-free
runs longer than 256 chars (W5, M-14: 26 R1 TEST items). The 256-character sliding D2 window missed
context-dependent PII (W4: a raw Aadhaar reached the client in streaming but not buffered mode). C-18
(LiteLLM #41611) is UNVERIFIED and is removed from this ADR's rationale.

**Decision.**
1. **Output mode is chosen per purpose by policy** (`policies/*.yaml` `data_class` → `stream_mode`).
   RESTRICTED, CONFIDENTIAL and unknown purposes use **buffered** output: the gate sees the whole
   response before anything is released. This is the industry default (C-01, C-03) and stays in force
   until incremental mode passes the pre-registered E-STREAM-LEAK gate (streamed output identical to
   buffered for every document and chunking).
2. **Incremental mode (INTERNAL only)** now uses: whole-line release (≤ 2,048 chars, then word cuts);
   a straddle guard that re-runs detectors over released context + pending text and never releases
   part of a detection; a two-word name hold-back; delimiter-only cuts outside aliases in long runs;
   and a gate that reads the whole released output (≤ 32 KB, then it degrades to buffered).
3. Regressions reproduce the R1 TEST shapes and were verified to fail on the R1 code
   (W5 194/200 corrupted, W4 100/100 leaked) and to pass on R2 (0/0).

**Status after R2 dev2:** incremental mode still fails E-STREAM-LEAK (23/1,730 documents differ,
13 stream-only fragments: right-context dependence across lines, far table headers). The residual is
why RESTRICTED stays buffered. See SECURITY_EVALUATION_R2.md for the held-out result.
