# Security boundary: what the gateway enforces, and what it does not

- **Date:** 2026-10-10. **Branch:** `security/mandatory-contracts`. **Pre-change snapshot:** `70d892f`
  (branch `backup/pre-mandatory-contracts`; local tag `pre-mandatory-contracts`).
- **Status:** not production-ready and not enterprise-ready. This document says exactly which properties the code
  enforces, and which are obligations on people or other systems.
- **Synthetic data only.** Nothing here authorises real HR data.

Each claim names the code that enforces it and the test that proves it. If a claim has no test, it is not a
guarantee.

## 1. Mandatory data contracts

| Property | Enforced by | Test |
|---|---|---|
| Every purpose in the registry is bound to a data contract; an uncontracted purpose or an app with no contracts stops startup | `check_startup_mode` (`pgw/gateway/app.py`) | `test_uncontracted_purpose_or_app_stops_startup` |
| A request for an uncontracted purpose is refused with 403 `no_data_contract` **before the body is read**. No provider call, no request content in the response, logs or ledger | `_chat`: contract is resolved right after authentication | `test_request_path_refuses_uncontracted_purpose_even_if_startup_check_is_bypassed`, `test_uncontracted_purpose_is_refused_before_the_body_is_read` |
| The shipped registry has no uncontracted app (`hr-assistant` was removed) | `registry/apps.yaml` | `test_production_registry_has_no_uncontracted_purpose` |
| The pre-contract ("legacy") path exists only in an explicit development mode. `legacy_uncontracted` is never read from the environment; `create_app` refuses it in production; the request path checks the mode again | `Settings.legacy_uncontracted`, `check_startup_mode`, `_chat` | `test_legacy_path_cannot_be_enabled_in_production`, `test_defaults_and_environment_cannot_enable_the_legacy_path`, `test_legacy_requests_are_refused_if_mode_changes_after_startup` |
| Compatibility tests reach the legacy path only through `tests/sealed.legacy_dev` (or the labelled `demo/demo_gateway.py`); no production module sets the flag | source scan | `test_only_explicit_development_helpers_enable_the_legacy_path` |

**Mode.** `PGW_MODE` is `production` by default, and the env default is also `production`. `development` is an
explicit opt-in that allows three things: an unsigned manifest, no manifest, and the legacy path. The trace hook
also requires `development`.

**Development mode is isolated from real providers.** It can only reach the mock provider. A real provider in
development needs `dev_allow_real_provider=True`, which:

- is set in code only, never from the environment, so an env-configured deployment cannot turn it on;
- is used only by the synthetic-data harness (`scripts/pilot_validation.py`) and the demo inspector;
- is refused in production.

Test: `test_development_mode_only_reaches_the_mock_provider`.

**What the mode check cannot do.** The gateway cannot detect an operator who falsely declares `development`.
Three measures make it visible instead:

- the mode is logged at startup (`test_startup_log_records_the_mode`);
- the mode is recorded on every audit-ledger line (`"mode"`);
- the mode is reported on `/healthz`.

Deployment review must check it.

## 2. Sealed, signed configuration

The registry, the policy pack, every data contract, the roster (production) and the deployment profile are sealed
in `policies/MANIFEST.json`, with the sha256 and version of each file.

| Property | Enforced by | Test |
|---|---|---|
| The registry (`registry/*.yaml`) is sealed with the policies and contracts. Adding a purpose or a model, rebinding a task, or replacing a key hash without re-sealing stops startup | `integrity.SEALED_GLOBS`, `_sealed_paths` | `test_unreviewed_change_stops_startup[registry *]` |
| Production requires an **Ed25519** signature over the manifest that verifies under `PGW_TRUSTED_CONFIG_PUBKEY`. The following are all refused: a missing key, an unsigned manifest, an attacker's re-seal (unsigned or under another key), and a manifest edited after signing | `integrity.verify(require_trusted=True)` | `test_resealing_without_the_trusted_key_does_not_help`, `test_repo_manifest_seals_the_shipped_registry_policy_and_contracts` |
| Unsigned configuration and a missing manifest are accepted only in development | `check_startup_mode` | `test_unsigned_config_is_development_only` |
| **Deployment profile.** In production every security-relevant setting (sealed file paths, provider kind and URLs, egress and private allowlists, provider-terms attestation and region, NER model/workers/queue/timeout, every size, time and concurrency limit) is taken from the signed `registry/deployment.yaml`. A differing environment or Settings value stops startup, naming the setting but not its value; unset or equal values are fine. Every Settings field is classified (profile / code-only / operational) in `pgw/gateway/settings.py` | `apply_deployment_profile`, `create_app` | `tests/test_deployment_profile.py`: `test_a_settings_value_differing_from_the_profile_stops_production[*]`, `test_an_environment_value_differing_from_the_profile_stops_production[*]`, `test_from_env_in_production_cannot_weaken_the_signed_profile[*]`, `test_every_settings_field_is_classified` |
| An edited, missing or unsigned profile, or a signed manifest that omits it, stops production | `integrity.verify`, `apply_deployment_profile` | `test_an_edited_profile_without_re_signing_stops_startup`, `test_a_missing_or_unsealed_profile_stops_production`, `test_malformed_profiles_are_refused[*]` |
| NER off in production is a signed decision: `ner_model: null` needs `ner_off_justification` (at least 20 characters) in the profile. A model that does not load still stops startup | `apply_deployment_profile` | `test_ner_off_without_a_signed_justification_stops_production[*]`, `test_ner_off_with_a_signed_justification_starts`, `test_ner_model_that_does_not_load_stops_startup` |
| In development the profile is optional; differences are logged as warnings and the Settings value is used | `apply_deployment_profile` | `test_development_tolerates_differences_with_warnings`, `test_development_without_a_profile_starts` |

**Workflow:**

1. Run `python -m pgw.product.integrity keygen KEYFILE` once. It creates an offline private key and prints the
   public key.
2. After each reviewed change, run `python -m pgw.product.integrity seal --sign-key KEYFILE`.
3. Deploy with `PGW_TRUSTED_CONFIG_PUBKEY=<hex>`.

**The manifest committed in this repository is sealed but unsigned.** It is valid for development only, so a
production deployment must be signed by its own approver. The older HMAC (`PGW_POLICY_SIGNING_KEY`) is still
verified if configured, but it is not a trust anchor: whoever holds the gateway's environment holds that key.

**Not enforceable by the gateway:**

- Anyone who can replace `PGW_TRUSTED_CONFIG_PUBKEY` can make the gateway trust their own signature. That public
  key must come from configuration the config authors cannot write (secret store, image, admission policy).
- Who may hold the private key, and how a change is reviewed before it is signed, is an organisational control.
- **No rollback protection.** An older manifest that was validly signed, together with its older files, still
  verifies. For example, a previous registry with a model or key hash that was later removed would be accepted. To
  prevent this, the deployment must pin what it ships (an immutable image digest) or rotate the signing key when
  revoking a configuration. The gateway keeps no monotonic config serial.
- Every sealed file is read once at startup, and the bytes that are verified are the bytes that are parsed. A file
  swapped on disk during startup cannot be served unverified (`test_verified_bytes_are_the_parsed_bytes`). A change
  on disk after startup has no effect until restart, and the restart re-verifies it.

## 3. Purpose → contract resolution (no tenant-specific contracts)

The contract map is **global and keyed by purpose**, and so is the policy pack's per-purpose action table. One
purpose means one contract for every app and every tenant.

- If two registry entries bind the same purpose to contract files with **different content**, startup stops. This
  holds even when the files carry the same `contract:` name and `version:`, because the check compares the sha256
  of the file bytes, not only `name@version` (`load_contracts`; test
  `test_same_purpose_same_ref_different_content_stops_startup`).
- Byte-identical files are accepted (`test_same_purpose_identical_content_is_accepted`).
- **Limitation:** tenant-specific contracts are not supported. Contract isolation per tenant is not claimed.
  Supporting it would need the contract lookup, the policy pack's action table and the alias namespace all keyed by
  `(tenant, app, purpose)`, with tests. That work is not done. `test_contract_map_is_global_not_tenant_scoped`
  pins the current behaviour.
- Keys **are** per tenant when a tenant key file is configured (`pgw.keys`): alias HMAC, restore ledger and audit
  MAC. That is key separation, not policy separation.

## 4. NOT_EXPECTED and the external-processing boundary

### What `purpose_dependent` is

Every contract states a stance on seven purpose-dependent attributes:

- leave balance;
- attendance;
- performance rating;
- health or leave reason;
- disciplinary outcome;
- eligibility result;
- derived compensation.

At load time the gateway validates that every stance is present and valid. **At runtime it does nothing with
them.** No code path reads `purpose_dependent` while handling a request.

**The gateway does not detect or remove medical, disciplinary or other NOT_EXPECTED details from free text.** Its
detectors find identifiers (names, IDs, contacts, government and financial numbers). They do not find these
attributes, and no keyword list has been added to pretend otherwise. The test
`test_not_expected_content_is_not_removed_when_attested` asserts this limitation. If attested free text contains
"chemotherapy" or "disciplinary warning", those words reach the provider.

### What is enforced instead

Free text reaches an external model only if the source system that produced it signs it. Each contract must declare:

```yaml
external_input:
  sanitizers:                                     # source-side exports/sanitizers this purpose accepts
    helpdesk-ticket-export@1: "<64-hex Ed25519 public key>"
  unattested: PRIVATE                             # or REFUSE
```

The contract is sealed and signed with the rest of the config, so the pinned keys are as trusted as the config.
The source system holds the private key. The calling app and the gateway never do.

Every request for a contracted purpose must carry four headers, on every route. That includes LOCAL_COMPUTE, whose
`instruction` field is free text that goes to the external model.

- `X-PGW-Input-Source: <id>`, `X-PGW-Input-Timestamp: <unix seconds>`, `X-PGW-Input-Nonce: <32-64 hex>`,
  `X-PGW-Input-Signature: <hex>`.
- The signature is Ed25519 over `pgw-input-attestation/v2` + newline + the JSON array
  `[app id, purpose, source id, timestamp, nonce, sha256]` (`pgw.product.attest.canonical_message`). The app id is
  the authenticated calling app, so one app cannot use an attestation signed for another.
- The hash is over the canonical JSON (sorted keys, no whitespace, ASCII escapes, no NaN or Infinity) of
  `{"messages": ..., "pgw_subjects": ... or null}`, as the gateway parsed the body. It therefore covers every
  message, including assistant history, tool calls and list-form content parts, plus their order and count, and the
  declared subjects.
- The gateway verifies before any send and before anything rewrites the messages. It rejects:
  - an unknown or missing source id, or a missing signature;
  - a timestamp more than 300 s from the gateway clock, either way (`MAX_SKEW_S`);
  - a signature by another key, for another app, purpose or source id, or over other content;
  - a message already accepted once (`replayed`). Each verified message is remembered until its timestamp leaves
    the window, in a bounded cache (`REPLAY_CACHE_MAX`, 100 000). When the cache is full of live entries the gateway
    answers 503 and sends nothing; it never evicts an entry that could still be replayed. Only verified messages
    enter the cache, so forgeries cannot fill it.
- **Where accepted messages are stored.**
  - **Production:** a persistent replay store is required (`PGW_REPLAY_DB`, a SQLite file in WAL mode); startup
    fails without one. Every gateway process on the host shares it, and it survives restarts. The check is an
    atomic primary-key insert, so two processes racing on the same message cannot both accept it.
  - **When the store fails:** any database error (unreadable, locked past 2 s, disk full) refuses the request
    with 503 `replay_store_unavailable`, and nothing is sent. A message is never accepted without being recorded.
    Tests: `test_replay_is_refused_across_restart_and_across_instances`,
    `test_replay_store_failure_refuses_the_request`.
  - **Development:** the in-memory per-process cache is allowed.
  - **Limit:** replicas on several hosts must not share one SQLite file over a network filesystem. They need a
    shared database with the same insert-or-fail semantics. That backend is not implemented; until it is, run
    every replica that serves attested purposes on hosts sharing a local store, or accept one replay per host
    within the window.
- On failure the contract's `unattested` action applies:
  - `REFUSE`: the response is 422 `input_not_source_sanitized` with a content-free `attestation` reason, nothing is
    sent, and the ledger records `BLOCKED`, reason `input_not_sanitized` and `input_attestation`.
  - `PRIVATE`: the input goes only to the in-boundary model, and the ledger records `SENT_PRIVATE`.
- A verified request's ledger line records `input_source` (the id) and `input_verified: true`.
- The shipped EXT_PSEUDO contracts default to `PRIVATE`. `PRIVATE` is allowed only on EXT_PSEUDO contracts, and the
  gateway does not start unless a private endpoint is configured. LOCAL_COMPUTE contracts keep `REFUSE`.
- **Demo keys.** The shipped contracts pin DEMO keys whose private halves are committed
  (`tests/fixtures/DEMO_ONLY_source_signing_keys.json`) so that demos and tests can sign. Production refuses to
  start with any of them (`pgw.product.attest.DEMO_PUBKEYS`). Development accepts them. Operators must replace them
  with their own sources' keys and re-sign the config (OPERATIONS.md).
- A contract without a valid `external_input` does not load. That includes a missing, malformed or all-zero public
  key and the old list form without keys.
- Tests: `tests/test_source_attestation.py`, `test_unattested_input_is_refused_before_any_send`,
  `test_local_compute_free_text_needs_the_attestation_too`, `test_unattested_input_can_be_kept_in_boundary`,
  `test_contracts_must_declare_a_valid_input_boundary`.

### What the attestation proves, and what it does not

- **Verified:** the holder of the source's private key signed exactly this content (messages and declared subjects)
  for this calling app, purpose and source id within the last five minutes, and the gateway process has not
  accepted it before. Knowing the public id is no longer enough. The calling app cannot add, alter or reorder
  content, reuse another request's or another app's signature, or resend the same signed request.
- **Not verified:** whether the source's sanitizer actually removed NOT_EXPECTED content. A signed ticket that still
  says "chemotherapy" is sent (`test_not_expected_content_is_not_removed_when_attested`).
- **Not verified: key custody at the source.** If the calling app, or anyone else, holds the source key, the
  guarantee reduces to that party's word. The gateway cannot tell.
- **Replay across hosts.** See the multi-host limit above. The conversation id is not signed; it is a routing
  hint for alias restore, not content.
- **Small-order keys.** Public keys that are small-order points (all eight, in any encoding) or non-canonically
  encoded are refused when the contract loads.
- **Interoperability.** A source implementation in another language must reproduce the canonical JSON exactly,
  including number formatting and `\uXXXX` escapes. A mismatch fails closed (refused or in-boundary), not open.

The NOT_EXPECTED guarantee therefore still rests on the source system. The candidates are:

- an allowlisted export of HRIS fields;
- a ticket form without free-text medical fields;
- a reviewed sanitizer owned by the source team, which holds the key.

When the source cannot reduce the input safely, the only safe options are refusal or the in-boundary model.

## 5. Evaluation coverage

`eval/business` is the **fixed-scenario acceptance regression suite** for the ten pilot scenarios (A1–A4, B1–B3,
M1–M3). It is not a general evaluator.

- **What a privacy PASS covers.** It means none of the **declared or expected canary values for that scenario**
  were found:
  - the scenario's `raw_values`: declared subjects and the identifiers the scenario expects to be protected;
  - its `never_returned` C3 values;
  - leftover aliases and raw redaction markers in the answer.

  It says nothing about any other personal data that may be in the text.
- **Partial captures are no longer a pass.** The privacy check is NOT_EVALUABLE unless every captured outbound
  exchange is present, is JSON, and has a `messages` list. Previously only the first exchange was inspected, and
  empty or non-JSON captures could pass. An empty restored answer is also NOT_EVALUABLE. A capture with more provider
  responses than captured requests is NOT_EVALUABLE too.
- Tests: `test_privacy_is_not_evaluable_unless_every_exchange_is_inspected`, `test_a_leak_in_a_later_exchange_fails`,
  `test_empty_answer_is_not_evaluable_not_a_privacy_pass`, `test_load_captures_keeps_every_outbound_exchange`.
- **Re-check of the existing Groq runs.** `final-groq` and `final2-groq` give the same per-case results under the
  stricter handling, because each of those cases had zero or one exchange.

## 6. Properties the gateway cannot enforce

1. **The content of free text.** NOT_EXPECTED attributes, and any personal data the detectors miss, cross if the app
   attests the input. Recall is measured in `SECURITY_EVALUATION.md` and is not complete.
2. **What a signed source did.** The gateway verifies which source key signed the input (§4). It does not
   verify that the source's sanitizer removed anything, or that only the source holds the key.
3. **The trust anchor.** The gateway cannot verify where `PGW_TRUSTED_CONFIG_PUBKEY` came from, or whether
   `PGW_MODE=development` is used outside development.
4. **Network-level egress.** The app must not be able to reach the provider around the gateway. That is the
   network policy in `deploy/`, not application code.
5. **Provider behaviour.** Retention and region are attested by the operator (`PGW_PROVIDER_*`), not verified.
6. **Tenant-specific policy.** Not supported (§3).
7. **Declared subjects' completeness.** People the app does not declare rely on detection.
8. **Gateway settings outside the signed profile.** Since the deployment profile, every security-relevant setting
   is signed and the environment can only repeat it. What still comes from the environment is operational: the
   mode (6.3), the trust anchor (6.3), the manifest path (any manifest it names must verify; rollback limit in §2),
   secrets and key files, ledger and audit-log locations, and the private model name. Code-only fields (injected
   upstreams, development switches) are never read from the environment. The gateway also cannot tell whether the
   signed values are right for the deployment (e.g. that the profile's allowlist matches the network policy, or that
   a provider attestation is true): that is review before signing.
9. **Values that cannot be declared.** Declared subjects cover name, employee ID, email and phone. A UAN, passport,
   voter ID, home address, an unusual ID format or a salary written as plain digits ("21,00,000") in free text is
   protected only if a detector finds it. Number-shaped identifiers of any scheme are found by shape (§7, S6), also
   when spoken digit by digit in words; amounts in Indian or Western digit grouping and amounts with lakh/crore (in
   digits or words) are found by shape (§7, S5). Addresses, PIN codes and small plain-digit amounts ("45000") are not.

## 7. Independent audit (2026-10): what was confirmed and what changed

An external audit attacked the code merged in `a465d55`. Every claim was re-checked with our own probes against
that commit (mock provider, synthetic data). The auditor's own scripts were not run.

| # | Finding | Verified | Status |
|---|---|---|---|
| S1 | Request parameters (`temperature`, `max_tokens`, `top_p`, `stream_options`, `tool_choice`, …) carried data to the provider unchecked | Confirmed | **Fixed.** Every parameter now has a strict type and range. The outgoing request is built from an explicit allowlist. Contracted purposes refuse `tools`, `tool_choice` and `stop`, which are free text (`check_parameters`, `tests/test_audit_regressions.py`) |
| S2 | `band_position`: the band name and limits were caller-supplied and crossed raw, so any text, or an exact salary to ₹1, could leave | Confirmed | **Fixed.** Pay bands are a sealed `reference` table in the signed contract; the caller only names a band. Contracts are refused if a C0 input is not a key of a sealed table, and table ranges must be at least 20% wide |
| S3 | `X-PGW-Input-Source` is a public constant; any caller passes | Confirmed (already documented, §4) | **Fixed for provenance.** The source signs (Ed25519, key pinned in the signed contract; small-order keys refused) the calling app, purpose, source id, a timestamp within ±300 s, a nonce and a hash of all messages and declared subjects. The bare id, other keys, other apps, purposes or ids, edited, reordered or extended content, stale timestamps and resends of an accepted message are rejected before any send. Unverified input is refused or, by default on EXT_PSEUDO, kept in-boundary. Production refuses the shipped demo keys. **Still open:** whether the source's sanitizer removed NOT_EXPECTED content, key custody at the source, and replay across replicas (the replay cache is per process, §4) |
| S4 | The roster was not sealed; an emptied roster disabled name matching. Security-relevant settings (NER, egress allowlist, limits, provider attestation) came from unsigned environment variables; NER off in production only logged a warning | Confirmed | **Fixed in code, with tests (§2).** Production seals the roster, and takes every security-relevant setting from the signed deployment profile `registry/deployment.yaml`; a differing environment value stops startup, and NER off needs a signed written justification. Residual: operational settings (mode, trust anchor, manifest path, secrets, log locations) still come from the environment (§6.8), and the rollback limit of §2 applies to the profile too |
| S5 | Free-text leaks on realistic tickets (auditor 13/36; our re-check 8/11) | Confirmed | **Open, structural.** Lowercase or undeclared names, UAN, passport, voter ID, address and plain-digit salaries leak. Declaring people as subjects removed the name leaks. **Amounts without a currency cue mitigated:** a number in Indian digit grouping (d{1,2}(,dd)+,ddd, so >= 1,00,000) or Western grouping >= 100,000, and a number phrase in words or digits with lakh/lac/crore ("twenty two lakh fifty thousand", "1.2 crore"), is COMPENSATION (C2: aliased and restored on EXT_PSEUDO, generalised elsewhere), unless the statutory-constant rule or sealed `public_constants` apply, or a unit of count follows ("1,50,000 employees"). Western grouping was measured: no gate difference on dev3build. Small plain-digit amounts ("45000") still need a pay cue (`tests/test_number_words_and_amounts.py`) |
| S6 | ID formats missed (auditor 11/15; our re-check 5/18: bare Aadhaar, spaced PAN, uncued account number, phone in words, IFSC by policy) | Partly confirmed | **Mitigated for number-shaped identifiers.** A shape backstop (`pgw/detect/idshape.py`) withholds, as IDENTIFIER (C3, REDACT, never restored), 9-18 digit runs, Verhoeff-valid Aadhaar in any grouping, and letter-and-digit codes (>= 4 digits, 7-20 characters), plain, spaced or hyphenated, whatever the scheme. Dates, times, amounts, versions, section/form references and IFSC are exempt by general rules; company reference formats only through the signed contract field `business_identifiers`, which refuses patterns that match generic identifiers. Identifiers spoken digit by digit (digit words, "double"/"triple", any case, joined by spaces, hyphens or commas, at least 6 digits in words) are rewritten as digits in a D0 view (`numwords`) that the scheme recognizers and the backstop read; spans map back to whole words. Still open: identifiers split across lines, or longer than 18 digits; numbers in words that are not digit by digit ("ninety-eight forty-five ..."); postal codes (`tests/test_idshape_backstop.py`, `tests/test_number_words_and_amounts.py`) |
| S7 | Medical or harassment details sent | Confirmed (expected, §4) | **Open, by design.** Enforced only at the source |
| C1 | Equal salaries were reported as one person "having the higher CTC" | Confirmed | **Fixed.** A fixed `ranking` sentence says "equal" on a tie |
| C2 | "USD 142,000" was banded as ₹0–5L | Confirmed | **Fixed.** Foreign-currency amounts get no INR band |
| C3/C4 | "Payment of Gratuity Act" aliased as a person; ₹7,000 statutory cap generalised | Confirmed | **Fixed by structural rules** (`tests/test_name_and_legal_consistency.py`). (a) A capitalised run ending in an instrument head word (Act, Rules, Regulations, Code, Scheme, Policy, Ordinance, Amendment, Bill, Order; optional year; "Code on …") is a title: cue and NER PERSON hits in it are dropped and partial-name propagation does not enter it. Roster, declared and ledger hits are never dropped; a name written in front of a title ("Priya's Leave Policy", or a NER name followed by more title words) stays a name. "of" after a capitalised word that does not start a sentence ("Head of Talent Acquisition") is no longer a name cue. (b) An amount governed in its clause by a limit or statutory construction (limit word on its left with no other amount between, "Rs X is the … ceiling", "under the … Act", "as per the Act", prescribed, statutory, notified, minimum wage) is a public constant, unless a specific person anchors it in the same clause (her/his/their, a possessive, a two-word name, "current/expected/offered … salary"): "her salary of Rs 21,00,000 exceeds the ceiling" stays compensation. Contracts may also seal exact `public_constants` (amounts only, at most 8 digits), held out of detection like `org_contacts`. Residual: a person named only in a different clause, or by a single word, does not anchor an amount |
| — | A partial name ("Priya", "Ms Raghunathan", "P. Raghunathan", "priya") got a different alias from the full name | Confirmed (mock provider) | **Fixed.** In a request, a partial mention takes the alias of the one detected or declared full name that contains it, and restores to that full name. Across turns, the restore ledger keeps keyed hashes of each full name's parts linked to its alias (`name_links`), so a later turn that writes only a part gets the same alias (and restores to the part it wrote: the ledger never stores names). **Ambiguity rule:** a part contained in two different full names (two people called Priya, now or earlier in the conversation) is never merged; it gets an alias of its own, stays replaced, and stays ambiguous for the rest of the conversation |
| C5/C6 | Bands shown in drafted emails; honorific guessed from a placeholder ("Mr. Priya") | Confirmed | **Mitigated in guidance.** `hr_email_draft` now tells the model not to copy bands and not to use honorifics. Bands are not restored by design |
| C7 | INSTALL.md said `dev.txt`/`ner.txt` include runtime packages | Confirmed | **Fixed** in INSTALL.md |

**What this means.** The audit's main verdict stands. Detection in free text leaks, so free text must not go to an
external model unless its source really reduces it. Run detection as a backstop, not as the control. The changes
above close the side channels the gateway itself was responsible for.

