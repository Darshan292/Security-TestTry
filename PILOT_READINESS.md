# Pilot Readiness Review: HR helpdesk slice

**Date:** 2026-10-02 (after Path A declared subjects). **Data:** synthetic only.

**Verdict:** not production-ready. This is not ready for real HR data. It can be demonstrated on synthetic data, but no real model answer has been validated. `ANTHROPIC_API_KEY` is still not configured in this environment, so every check that needs a model answer is **BLOCKED**, not passed.

## A. What is proven

The table covers 6 tasks:
- **Path A** (external model on protected text): A1 ticket summary, A2 case explanation, A3 email draft, A4 a ticket whose names NER misses.
- **Path B** (local compute first): B1 salary comparison, B2 ESI letter.

| # | Property | Evidence |
|---|---|---|
| 1 | Raw protected values never leave | **Real endpoint.** None of 52 values in the exact HTTP bytes sent to `api.anthropic.com` (raw or JSON-decoded) |
| 2 | Declared subjects are protected even where NER misses them | **Real endpoint, A4.** The existing detectors (incl. en_core_web_lg) find **0 of 2** names in that text; after declaration **0 leaked** |
| 3 | Local-compute inputs never leave | **Real endpoint, B1/B2.** 27 values checked (identities plus every written form of the exact CTCs, wage and INTERNAL outputs): 0 found; only contract-declared results cross |
| 5 | Provider failure fails closed | **Real endpoint.** Invalid key gives 502; timeout gives 502. No content is returned |
| 6 | Egress violations fail closed | Pipeline leak gives 500, and a declared-subject leak (declarations dropped) gives 500. Nothing sent in either case |
| 7 | No sensitive value in logs, stderr, ledger or error bodies | 42 values, 0 found |

How declared subjects work:
- The app sends requester, assignee, mentioned employees and other subjects as `pgw_subjects`. Their schema is fixed per contract and sealed in the manifest.
- Every written variant is aliased, without relying on detection. That covers case, joins, handles, Unicode folding, IDs with any separator, phone groupings, and compound names.
- The same matchers then sweep the outbound bytes; any hit fails closed.
- Aliases are scoped per contract.
- Only the contract's restore classes are returned to the app, using the declared spelling.

Robustness:
- An adversarial review produced 17 reproduced findings. Each is fixed, fails closed, or is documented below.
- Each has a regression test in `tests/test_declared_subjects.py`.
- Full suite: 1,569 passed.

The other gateway failures were also exercised: detector crash 503, deadline 503, non-allowlisted provider refused at startup. Every request writes a content-free ledger row with the contract version, route, integrity digest and subject count.

## B. What is still unproven

- **#4, restoration on a real provider response.** Alias survival, restoration and answer quality are proven only against the mock: 55/55 checks, including A4 restoring the names NER missed. This is BLOCKED until a key exists.
- **Human review** of the six restored answers.
- **Latency with a real model.** Gateway overhead is about 22–54 ms per request excluding the provider, but buffered output with adaptive thinking has not been measured.
- **Network isolation.** The compose and k8s configs have never run.
- **Multi-turn** on contract paths.

## C. Remaining blockers

1. **Undeclared people and identifiers still depend on detection.** An unknown name in lowercase or unusual form leaves raw, and NER is a secondary net only.
2. **Indirect identifiers cross by design:** pronouns, roles, dates, ticket IDs, leave balances, and the ESI yes/no. In small teams these re-identify people, which needs a DPIA decision.
3. **Accepted residuals in declared matching:**
   - single initials ("T. Kulkarni" sends "T.");
   - name parts that are common English words ("Will", "Do") are masked only when capitalised;
   - homoglyphs from other scripts are left to the C4 refusal;
   - escaped JSON inside content fails closed (500) instead of being masked.
4. **Utility cost from the existing NER:** it over-masks some words, e.g. "Raised" in "Raised by:" became a PERSON alias in the captured payload. No detector change was made, by instruction.
5. **Ops:**
   - no CI;
   - JSONL/SQLite audit storage with no alerting;
   - one master key for all tenants, and demo app keys;
   - unsigned manifest by default; the registry is not sealed;
   - no load test;
   - provider retention terms not settled.

## D. Is the synthetic-data demo ready for an ESU architect?

**Yes, for the gateway mechanics:**
- the exact bytes that reached the real endpoint for all six tasks, next to what the app sent;
- A4, where NER finds 0 of 2 names and the declaration protects both;
- Path B, where salaries never leave;
- the fail-closed matrix;
- startup refusals on a tampered or unsafe contract.

**No, for model answers:** without a key, restored real answers cannot be shown. Do not claim answer quality, anonymisation, network isolation or production readiness.

## E. The single next product capability

Strictly speaking, the next step is not a capability: **run `python -m scripts.pilot_validation` with a key**, then have a human read the six answers. That one command closes B#4.

The next capability after that is **multi-turn conversations on contract paths with declared subjects**. Today each request is protected on its own. A real helpdesk thread replies with prior turns, so aliases must stay stable across turns, using the existing restore ledger plus the per-contract alias scope. Earlier restored answers must also be re-protected when the app sends them back. This is untested today and is the next leak surface a pilot will hit.
