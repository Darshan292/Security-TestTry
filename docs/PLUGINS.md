# Plugins: outside libraries behind plugin slots

Status: built and tested; **every plugin is off** in the shipped profile. Product rule: nothing existing is removed.
An outside component runs side by side with the built-in detection, and it can only become authoritative after it wins
on held-out tests: a human decision, made in a reviewed, signed configuration change.

Code: `pgw/plugins/` (slots `base.py`, adapters `adapters.py`, shape candidates `candidates.py`, configuration and
runtime `registry.py`). Tests: `tests/test_plugins.py`. Comparison: `scripts/compare_validators.py`.

## 1. Slots

| Slot | Interface | Adapters |
|---|---|---|
| Validator | `validate(entity_type, candidate_text) -> VALID \| INVALID \| UNKNOWN` | `builtin` (reference, never configured: the repo's Verhoeff, PAN holder-type pattern, IFSC pattern, Luhn + issuer prefix, UPI, phone patterns, IBAN mod-97 + length), `stdnum` (python-stdnum: `stdnum.in_.aadhaar`, `vid`, `pan`, `gstin`, `epic`, `stdnum.iban`, `stdnum.luhn` for cards framed as 13-19 digits), `phonenumbers` (region IN; VALID = parses and `is_valid_number`) |
| Recognizer | `find(text) -> [(start, end)]` of its `entity_type` | `detect_secrets` (SECRET): every regex-based detect-secrets plugin except `IPPublicDetector`, run in-process on the text |

UNKNOWN means "no opinion" (a scheme the validator does not cover): it is never a disagreement. The built-in has no
GSTIN, EPIC or VID validator; stdnum has no IFSC, UPI or phone module.

**detect-secrets, how it is used.** Only the plugin classes' regexes, on the request text: no file scanning, no
baseline, no settings file, and never `verify()` (it calls vendor APIs). A finding is mapped to the span of the whole
regex match, not to the capture group detect-secrets reports as the "secret": for `GitHubTokenDetector` that group is
only the prefix `ghp`, so masking the reported value would leave the token in the text. The whole match can include a
delimiter or a key name (`NpmDetector` matches to the end of the line); masking more only reduces leaks.
`import detect_secrets.plugins.base` imports `requests` at module import; nothing in pgw calls it. Two of its regexes
are quadratic on adversarial input (16 KB of `/` costs `NpmDetector` about 1 s; `eyJ` repeated without a dot does the
same to `JwtTokenDetector`). The adapter does not change them; it only decides where they run (lines that can match,
JWT-alphabet runs up to 4 KB), which keeps every possible match and makes the cost linear.

## 2. Modes (signed deployment profile only)

```yaml
# registry/deployment.yaml, settings:
plugins:
  stdnum: "off"            # validator: off | shadow
  phonenumbers: "off"      # validator: off | shadow
  detect_secrets: "off"    # recognizer: off | shadow | union
```

`plugins` is a profile field (`FIELD_CLASS`, not read from the environment). Quote the mode: YAML reads a bare `off`
as `false`, which is refused with that hint. An unknown plugin name, a mode the slot does not offer, or a configured
plugin whose library is not installed stops startup.

- **off** (default): not built, its library not even imported.
- **shadow**: runs on every inspected string after the built-in detection and is compared with it. Validators run
  only on shape candidates (substrings with the right length and characters for a scheme, at most 256 per string),
  never over the whole text. Recorded: counts by plugin, entity type and verdict pair (for example
  `stdnum|disagree|CARD|INVALID|VALID`, `detect_secrets|disagree|SECRET|NONE|FOUND`), plus `compared`,
  `plugin_only` (built-in has no opinion), `error` and `capped`, in process metrics
  (`app.state.pgw_plugin_metrics.snapshot()`) and as `event='plugin_shadow'` log lines. Never the value, never a hash
  of it. Shadow output is byte-identical to off (tested), and shadow results are not in the transparency report.
  A shadow plugin that raises is counted (`error`) and has no effect on the request.
- **union** (recognizers only): the plugin's spans are added to detection and go through the existing overlap
  resolution with scores chosen so that no character the built-in masks is ever released: a span inside an existing
  detection adds nothing; a span that partly overlaps one loses to it; a span that only contains existing detections
  replaces them with one larger SECRET span. Union spans are ordinary detections afterwards: policy action, alias,
  propagation, the egress check, and the report (`source: "detected"`). A union plugin that raises fails the request
  closed with 503 `detector_unavailable`, as the ML detector does.
- **enforce is not offered for validators.** Switching the authority for what counts as an Aadhaar (or a PAN, a card)
  changes what is masked and what is released. The disagreements below show both directions exist; which side is
  right is decided by humans after a held-out comparison, not by a configuration switch. When that happens, it is a
  code change that adds the mode, reviewed like any detector change.

## 3. How a plugin is promoted

1. Run it in shadow in a pilot; read the disagreement counts (operator metrics).
2. Investigate each disagreement class with `scripts/compare_validators.py` on DEV data and the specs (section 4).
3. A human evaluator runs the held-out comparison (EVALUATION_PLAN.md rules; this repository's tools never read
   held-out sets). The result and the decision are recorded in an ADR.
4. The mode change is a profile edit: bump `version` and `config_serial`, code-owner review, re-seal and sign
   (OPERATIONS.md). For validators, adding an authoritative mode is additionally a code change.

## 4. Findings: built-in vs stdnum vs phonenumbers (DEV data, synthetic generators, spec vectors)

`python scripts/compare_validators.py` (2000 generated values per type plus mutants and edge vectors; DEV corpora
`eval/data/dev.jsonl`, `tune.jsonl`, `eval/data3/dev3build.jsonl`; held-out, test, tester and dev3val sets are
refused by the script). Verdict triples are (builtin, stdnum, phonenumbers).

| Type | Class | Counts | Who is right (spec) | Action |
|---|---|---|---|---|
| AADHAAR | Verhoeff-valid palindromes (`200009900002`): builtin VALID, stdnum INVALID | 20/20 edge vectors; 0 in 1588 generated and 715 corpus Aadhaars | stdnum enforces "an Aadhaar cannot be a palindrome", citing the UIDAI numbering-scheme working paper; that rule could not be checked against a UIDAI publication from here. UIDAI's public structure (12 digits, first digit 2-9, Verhoeff) is what both implement | Unresolved; built-in's acceptance is the masking-safe side. Documented by a plain test |
| AADHAAR | dot-grouped values (`2497.4650.7527`) | 412 generated | Both reject (separator is not space/hyphen); the D0 views handle dots in detection | No disagreement |
| CARD | Luhn-valid cards of JCB (3528-3589), UnionPay (62), Diners (300-305), Discover (644-649), Maestro (67): builtin INVALID, stdnum VALID | 6/12 edge vectors (networks' published test numbers) | **Built-in is wrong**: `recognizers._iin_ok` omits issued ISO/IEC 7812 ranges, so such cards are not recognised as CARD (they may still be caught as IDENTIFIER or BANK_ACCOUNT) | xfail `test_builtin_card_accepts_every_issued_network`; not changed silently: in the DEV corpora 273 Luhn-valid 13-19 digit runs that are not cards would also pass a Luhn-only check, so a fix needs a measured precision decision |
| CARD | corpus Luhn-valid non-card runs (bank accounts, IDs): builtin INVALID, stdnum VALID | 273 corpus candidates, none labelled CARD | Built-in right for detection (a Luhn pass alone is 1 in 10 for any number) | None |
| PAN | 4th character K: builtin INVALID, stdnum VALID | 174/2000 generated with holder K; 1 edge | **stdnum is wrong** against the Income Tax Department's holder list (P C H F A T B L J G); stdnum itself notes K "may have been discontinued" | xfail `test_stdnum_pan_rejects_holder_type_k` |
| PAN | sequential number 0000: builtin VALID, stdnum INVALID | 1 edge | **Built-in is lenient**: the sequence runs 0001-9999. Over-masks only | xfail `test_builtin_pan_rejects_serial_0000` |
| IBAN | country length right, BBAN format wrong: builtin VALID, stdnum INVALID | 191/200 random-BBAN IBANs | **Built-in is lenient**: it checks mod-97 and length, not the country's registered BBAN format (SWIFT IBAN registry). Over-masks only | xfail `test_builtin_iban_checks_the_bban_format` |
| PHONE | dates and ID fragments (`2026-10-28 14`, `9999-9999-99`): builtin INVALID, phonenumbers VALID | 2,585 corpus candidates not labelled PHONE (2,095 of them date-time shaped) | Both are right by their own definition: phonenumbers validates against the numbering plan (India's fixed-line plan accepts most 10-digit strings with a valid area code), not whether text is a phone number. As a detector it would massively over-mask | Documented by a test; never a candidate for authority on its own |
| PHONE | landlines whose subscriber number starts with 0, 1, 8 or 9 (`033-9123 4567`): builtin VALID, phonenumbers INVALID | 34 corpus PHONE spans | **Built-in is lenient** against India's National Numbering Plan (fixed-line subscriber numbers start 2-6); the corpus generator produces such numbers too. Over-masks only | xfail `test_builtin_landline_subscriber_first_digit` |
| PHONE | `0091 98765 43210`, `(91 ...)`: builtin validator INVALID, phonenumbers VALID | 21 corpus PHONE spans | phonenumbers right (00 is India's international prefix); the built-in recogniser still masks the 10-digit number inside (checked), so nothing leaks | None |
| PHONE | `5876543210`, `1800-123-4567`: builtin INVALID, phonenumbers VALID | 2 edge | Fixed-line without trunk 0 and toll-free are valid numbers; not personal mobiles | None |
| GSTIN, EPIC, VID | stdnum only (builtin UNKNOWN) | 2000 + 2000 generated, edge vectors | stdnum agrees with every spec vector | Coverage, not disagreement |
| IFSC, UPI_ID | builtin only (stdnum, phonenumbers UNKNOWN) | | | Coverage |

Where the two agree (generated Aadhaar, PAN with listed holder types, cards of listed networks, mutants with a wrong
check digit, IBAN spec examples, corpus-labelled Aadhaar/PAN/card spans), they agree completely.

## 5. Overhead (100 KB ticket)

A 100 KB ticket built from DEV corpus texts (synthetic HR tickets), one process, fastest of five runs:

| Configuration | `plugins.apply` alone | `Protector.detect_text` (whole detection of the string) |
|---|---|---|
| off | 0 ms | 2.76 s |
| shadow: stdnum + phonenumbers + detect_secrets | 66 ms | 2.89 s |
| union: detect_secrets | 57 ms | 2.88 s |
| shadow validators + union detect_secrets | 66 ms | 2.68-2.86 s (within run-to-run noise) |

The plugins add about 60 ms per 100 KB (2-3 % of detection; the default profile caps content at 50 000 characters
per request). On the adversarial inputs of tests/test_dos_bounds.py (100 KB each) plus inputs aimed at the
detect-secrets regexes, `plugins.apply` stays under 0.13 s; tests/test_plugins.py bounds it at a third of the 1.5 s
detection budget.

## 6. Licences

Read from each wheel's METADATA, recorded in THIRD_PARTY_NOTICES.md: python-stdnum 2.2 **LGPL-2.1-or-later** (used
as an unmodified installed library, never vendored or modified), phonenumbers 9.0.42 Apache-2.0, detect-secrets 1.5.0
Apache-2.0; transitive: requests Apache-2.0, urllib3 MIT, charset-normalizer MIT, idna BSD-3-Clause, certifi MPL-2.0
and PyYAML MIT (the last three already in runtime.txt). Locked with hashes in `requirements/plugins.txt` (compiled
with `-c runtime.txt`); not part of `runtime.txt`, so a gateway without plugins does not install them.
