# PD-13 development log (dev3build only)

One row per measured run of `python -m eval.run_v3 --arm X --split dev3build` (the tail-line numbers; full JSON in
`eval/results/v3_{A,B,C}_dev3build.json`, which holds the last run). Columns: CSVE, CSVE-HIGH, character leak,
negative-FP item rate, collateral masking, detection p95 (ms). `dev3val` and `test3` were not run, and their items were not read.

Targets (guidance only): CSVE far below A (0.3978); negFP <= 1.5 x A (0.717); collateral <= 5 %; p95 <= 250 ms.
Times are UTC wall-clock read from `date` after the run; rows 1-3 were run before the clock was being recorded (marked "n/r").
Arm C was started to be measured from row 4 on; its earlier rows do not exist.

| # | time (UTC) | arm | what changed | CSVE | CSVE-HIGH | char leak | negFP | collateral | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| 0 | n/r | A | baseline, untouched (reproduces the pre-registered 0.3978) | 0.3978 | 0.2367 | 0.2279 | 0.4778 | 0.0250 | 97.2 |
| 1 | n/r | B | first full build: L0+ views, L1 parsers (json, xml, delimited, fixed, kv, indent), L2 shapes, L3 standard dictionary (211 entries) + column statistics, L4 | 0.3444 | 0.1233 | 0.1778 | 0.6722 | 0.0894 | 111.5 |
| 2 | n/r | B | shapes allowed after a CSV comma; relaxed header test; roster EDM on a one-value-per-line view | 0.2678 | 0.0989 | 0.1053 | 0.6722 | 0.0884 | 121.9 |
| 3 | n/r | B | NER per name-shaped cell; HIGH_ENTROPY excludes word-identifiers; bilingual key segments; weak name evidence (name-shaped value); header comment marker stripped; 28 dev3 dictionary entries | 0.2144 | 0.0567 | 0.0818 | 0.5611 | 0.0735 | 121.3 |
| 4 | 10:04 | B | Unicode-aware name shape; key-position and own-line name cells; `Rs` marker needs `.`/space; 2-decimal amounts need 3 integer digits; compact dates not hyphen/slash-flanked; dotted version/IP is not LONG_DIGITS | 0.1844 | 0.0567 | 0.0714 | 0.5722 | 0.0688 | 125.4 |
| 4 | 10:04 | C | same code, `l3=False` | 0.3600 | 0.1433 | 0.1301 | 0.5500 | 0.0697 | 120.7 |
| 5 | 10:11 | B | line-unwrap tightened; digit-free tokens need 4.0 bits; `SHA384`-style word parts; table rows may be non-adjacent; fixed-width gaps tolerate a title line, chunks assigned to columns | 0.1711 | 0.0522 | 0.0580 | 0.5444 | 0.0664 | 130.7 |
| 5 | 10:11 | C | same code, `l3=False` | 0.3556 | 0.1467 | 0.1222 | 0.5222 | 0.0661 | 128.0 |
| 6 | 10:18 | B | a lone trailing delimiter keeps the cell count; non-Latin keys; unwrap only for soft breaks, digit groups and addresses; `id_gap` view; digits_joined shapes only for space/hyphen groups; plain numeric field values (>= 4 digits, not a year) are AMOUNT candidates; 17 dev3 RELEASE_ID entries | 0.1167 | 0.0244 | 0.0368 | 0.4556 | 0.0595 | 127.6 |
| 6 | 10:18 | C | same code, `l3=False` | 0.2522 | 0.0467 | 0.0735 | 0.5056 | 0.0668 | 127.4 |
| 7 | 10:22 | B | one-row tables (>= 5 cells); leading comment marker stripped from name cells; multi-argument calls parse as arrays; role-word (`anyword`) dev3 entries | 0.0967 | 0.0189 | 0.0296 | 0.4556 | 0.0600 | 126.1 |
| 7 | 10:22 | C | same code, `l3=False` | 0.2344 | 0.0400 | 0.0671 | 0.5056 | 0.0668 | 122.7 |
| 8 | 10:28 | A | final tree, untouched architecture | 0.3978 | 0.2367 | 0.2279 | 0.4778 | 0.0250 | 98.2 |
| 8 | 10:28 | B | stricter credibility for comma/semicolon tables; closing brackets stripped from kv values; `;`-separated multi-word keys; call-expression discovery; word-identifier parts >= 3 letters; one-line tables fixed | 0.0833 | 0.0089 | 0.0265 | 0.4556 | 0.0550 | 132.1 |
| 8 | 10:28 | C | same code, `l3=False` | 0.2233 | 0.0311 | 0.0639 | 0.5056 | 0.0617 | 128.4 |

Stop rule: after run 8 the remaining exposures on dev3build are PERSON values the spaCy NER cannot see (non-Latin scripts, names in
free prose, names inside code string literals) and unlabelled plain numbers in headerless tables. Closing them needs either an ML name
detector for those scripts or per-layout rules; neither is in the minimum experiment.
