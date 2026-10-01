# Corpus v3 — train / dev / test separation (pre-registered 2026-10-01)

## Splits

| Split | Families | Use |
|---|---|---|
| (train) | — | No component is trained on v3. If a local ML component is ever fine-tuned, it uses dev3-build only, and that is recorded |
| **dev3-build** | 12 (4 per group) | Development: read freely, error analysis allowed, dictionary entries may be derived (tagged `dev3:`) |
| **dev3-val** | 6 (2 per group) | Family-held-out check inside dev. Only `eval.run_v3 --split dev3val` aggregate metrics may be read, no items, at most 5 runs, each logged in `eval/results/v3_devval_log.jsonl` |
| **test3** | 18 (6 per group) | One-shot per arm (A, B, C) from one commit. Sealed until the run |

## Assignment (no human choice)

1. The catalogue lists 36 families (12 per group) and is sealed before assignment.
2. Seed = the first 16 hex characters of SHA-256 over the concatenation of:
   - the git commit id of the commit that registers this file, and
   - the SHA-256 of the sealed catalogue.
3. Within each group, families are shuffled with `random.Random(seed)` in catalogue order. Positions 0–3 go to dev3-build, 4–5 to dev3-val and 6–11 to test3.
4. The assignment is written into the sealed archive. Only the **dev** family ids are published.

## Other separations

- External-name tokens are disjoint between dev3 (build + val) and test3. Roster employees are shared.
- Identifier values are unique across the corpus.
- Adversarial kinds run on test3 only. dev3 gets only the `known` kinds; `heldout` kinds stay sealed.
- The ratio of negative to positive items is the same in every split, by construction (15/75 per family).
