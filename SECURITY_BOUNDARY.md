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
| **Rollback (re-audit N5).** The signed profile carries a monotonic integer `config_serial`. Production refuses an older, validly signed tree by two independent floors: (1) the pinned minimum `PGW_MIN_CONFIG_SERIAL` (operational, same custody as the trust anchor), which holds on a fresh host or an empty state directory; (2) the high-water mark `<PGW_STATE_DIR>/config_serial.json`, the highest serial this state directory has started with. Production requires a serial, the minimum and an existing state directory. Equal and higher serials start | `pgw/gateway/rollback.py`, `create_app` | `tests/test_reaudit_control_plane.py`: `test_audit_scenario_older_weaker_signed_tree_is_refused_by_both_mechanisms`, `test_equal_and_higher_serials_start_and_raise_the_mark`, `test_production_requires_serial_pinned_minimum_and_existing_state_dir` |
| The high-water mark is written only after every other startup check passed, atomically (temp file, fsync, rename, directory fsync) under an exclusive lock, and re-checked under the lock. A missing file is initialised (logged `config_high_water_initialised`). An unreadable or corrupt file stops startup and is never rewritten. Development never writes it and only warns about a serial below a configured floor | `rollback.check`, `rollback.record` | `test_corrupt_high_water_mark_fails_closed_and_is_not_reset[*]`, `test_unreadable_high_water_mark_fails_closed`, `test_high_water_write_is_atomic_and_leaves_no_temp_file`, `test_development_warns_and_never_writes_the_mark` |

**Workflow:**

1. Run `python -m pgw.product.integrity keygen KEYFILE` once. It creates an offline private key and prints the
   public key.
2. After each reviewed change, run `python -m pgw.product.integrity seal --sign-key KEYFILE`.
3. Deploy with `PGW_TRUSTED_CONFIG_PUBKEY=<hex>`, `PGW_MIN_CONFIG_SERIAL=<the profile's config_serial>` and
   `PGW_STATE_DIR=<existing persistent directory>`. Every signed change raises `config_serial` in
   `registry/deployment.yaml`. A deliberate rollback is a new signed change: the old content, re-signed with a serial
   above the current one (OPERATIONS.md).

**The manifest committed in this repository is sealed but unsigned.** It is valid for development only, so a
production deployment must be signed by its own approver. The older HMAC (`PGW_POLICY_SIGNING_KEY`) is still
verified if configured, but it is not a trust anchor: whoever holds the gateway's environment holds that key.

**Not enforceable by the gateway:**

- Anyone who can replace `PGW_TRUSTED_CONFIG_PUBKEY` can make the gateway trust their own signature. That public
  key must come from configuration the config authors cannot write (secret store, image, admission policy).
- Who may hold the private key, and how a change is reviewed before it is signed, is an organisational control.
- **Rollback limits.** The serial floors refuse an older signed tree only if the operator raises
  `PGW_MIN_CONFIG_SERIAL` with each release or keeps the state directory. Whoever can lower
  `PGW_MIN_CONFIG_SERIAL` *and* delete or replace `config_serial.json` (a host-level attacker, or a fresh host whose
  minimum was left at an old value) can still start an older signed tree; so can anyone who re-signs with the
  private key. Two different trees signed with the same serial are not told apart. The high-water mark is per state
  directory, not shared between hosts. Pinning the shipped image digest and rotating the signing key when revoking a
  configuration remain useful complements.
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
- **The in-boundary endpoint must be a real model (re-audit N2).** `pgw/private_llm.py` without `PGW_PRIVATE_GGUF`
  is an extractive stand-in that returns the ticket's first sentences; it is not a language model. When any contract
  routes to PRIVATE (`unattested: PRIVATE` or `C4: PRIVATE`), startup probes the endpoint's identity with
  `GET /v1/models` (`probe_private`, `pgw/gateway/app.py`). The stand-in always reports the id `extractive-standin`
  and `pgw_backend: standin` (fixed in code, also on every reply), so a configuration value cannot make it pass.
  - **Production** refuses the stand-in, an endpoint that cannot be probed or answers with something other than an
    OpenAI-style model list, and an injected endpoint with no probe. A reply whose `model`/`pgw_backend` is the
    stand-in's (the endpoint changed after startup) is withheld with 503 `private_model_unavailable`.
  - **Development** allows the stand-in with a repeated `PRIVATE_MODEL_IS_EXTRACTIVE_STANDIN_NOT_AN_LLM` startup
    warning. Every private-route reply carries `X-PGW-Private-Model: standin` (or `model`, or `unverified` when the
    development probe failed).
  - A real deployment runs an OpenAI-compatible model server inside the boundary (vLLM, Ollama, llama.cpp server)
    whose `/v1/models` lists `PGW_PRIVATE_MODEL`. Answer quality of that model is not evaluated here; it must be
    evaluated with HR staff before the route is relied on (OPERATIONS.md).
  - Tests: `test_production_refuses_the_extractive_stand_in`,
    `test_production_refuses_the_stand_in_even_when_the_model_name_claims_otherwise`,
    `test_production_refuses_an_endpoint_whose_identity_cannot_be_verified[*]`,
    `test_production_withholds_a_stand_in_reply_after_the_endpoint_changed`,
    `test_development_allows_the_stand_in_loudly_and_marks_every_reply`.
  - **Not verified:** that a model which is not the stand-in is any good, or that an endpoint lying about its
    identity is not a stand-in. The probe stops an accident, not a deliberate fake.
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
3. **The trust anchor.** The gateway cannot verify where `PGW_TRUSTED_CONFIG_PUBKEY` and `PGW_MIN_CONFIG_SERIAL`
   came from, or whether `PGW_MODE=development` is used outside development.
4. **Network-level egress.** The app must not be able to reach the provider around the gateway. That is the
   network policy in `deploy/`, not application code.
5. **Provider behaviour.** Retention and region are attested by the operator (`PGW_PROVIDER_*`), not verified.
6. **Tenant-specific policy.** Not supported (§3).
7. **Declared subjects' completeness.** People the app does not declare rely on detection.
8. **Gateway settings outside the signed profile.** Since the deployment profile, every security-relevant setting
   is signed and the environment can only repeat it. What still comes from the environment is operational: the
   mode (6.3), the trust anchor and the rollback floor `PGW_MIN_CONFIG_SERIAL` (6.3), the manifest path (any
   manifest it names must verify and pass the serial floors; limits in §2), the state directory, secrets and key
   files, ledger and audit-log locations, and the private model name. Code-only fields (injected
   upstreams, development switches) are never read from the environment. The gateway also cannot tell whether the
   signed values are right for the deployment (e.g. that the profile's allowlist matches the network policy, or that
   a provider attestation is true): that is review before signing.
9. **Values that cannot be declared.** Declared subjects cover name, employee ID, email and phone. A UAN, passport,
   voter ID, home address, an unusual ID format or a salary written as plain digits ("21,00,000") in free text is
   protected only if a detector finds it. Number-shaped identifiers of any scheme are found by shape (§7, S6), also
   when spoken digit by digit in words; amounts in Indian or Western digit grouping and amounts with lakh/crore (in
   digits or words) are found by shape (§7, S5). Addresses, PIN codes and small plain-digit amounts ("45000") are not.
10. **What an unrecognised identifier is.** The shape backstop cannot tell a ticket number from a passport or a
   cue-less account number. Both become IDENTIFIER (class REF): the provider sees a scoped alias, and on the contracted
   free-text purposes the reader gets the exact value back (§7, N4). So a personal identifier of a scheme no
   recognizer knows is returned to the requester who sent it; it never reaches the provider. A cue-less 12-digit
   number that passes the Aadhaar checksum is AADHAAR_LIKE (C3) and is never returned.
11. **Restating a withheld number in another unit.** LOCAL_COMPUTE compares every number in the instruction with the
   withheld values (§7, N6). A value encoded some other way (arithmetic, "my CTC minus five", a value spread over
   two numbers, digits in a non-Latin script word form) is not recognised.

**Decision: structured C3 types stay REDACTED on free-text purposes, never aliased or restored (N4, 2026-10-27).**
Aliasing PAN, Aadhaar, bank account, card, UPI and secrets and restoring them to the reader would keep the provider
blind as it does now, and would give the reader the value back. We do not do it, and the contract loader still refuses
C3 in `restore` and refuses `ALIAS` for C3:
- *Where the answer goes.* Answers are drafted emails, summaries and case notes that people forward. A restored
  Aadhaar or account number would land in text whose readers never had it. Names and salaries are restored because
  the task needs them; a summary or email rarely needs a full government or financial identifier.
- *Where a model puts a placeholder.* The model can copy an alias into a place the requester did not intend (a
  greeting, a subject line, a list of "contact details"). For C1/C2/REF the cost of that is a wrong but known value
  to the same reader; for a card, account or secret it is a disclosure we cannot take back.
- *The gate.* C3 types are what the output gate masks in answers (raw C3 is never returned, whichever model wrote
  it). Restoring them would mean switching that gate off for exactly the values it exists for.
- *Precision.* C3 recognizers are scheme-specific and checksum- or cue-based, so they rarely mask harmless text. The
  over-masking the re-audit saw came from the shape backstop, which is why only IDENTIFIER became restorable.
The cost is utility: an answer refers to "the PAN on file" instead of quoting it. A future contract-level opt-in
would need its own data class, its own gate exemption and a review; it is not offered in this release.

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
| S6 | ID formats missed (auditor 11/15; our re-check 5/18: bare Aadhaar, spaced PAN, uncued account number, phone in words, IFSC by policy) | Partly confirmed | **Mitigated for number-shaped identifiers.** A shape backstop (`pgw/detect/idshape.py`) withholds, as IDENTIFIER (since N4: class REF, aliased and restored on the free-text contracts; AADHAAR_LIKE for checksum-valid 12-digit numbers, C3), 9-18 digit runs, Verhoeff-valid Aadhaar in any grouping, and letter-and-digit codes (>= 4 digits, 7-20 characters), plain, spaced or hyphenated, whatever the scheme. Dates, times, amounts, versions, section/form references and IFSC are exempt by general rules; company reference formats only through the signed contract field `business_identifiers`, which refuses patterns that match generic identifiers. Identifiers spoken digit by digit (digit words, "double"/"triple", any case, joined by spaces, hyphens or commas, at least 6 digits in words) are rewritten as digits in a D0 view (`numwords`) that the scheme recognizers and the backstop read; spans map back to whole words. Still open: identifiers split across lines, or longer than 18 digits; numbers in words that are not digit by digit ("ninety-eight forty-five ..."); postal codes (`tests/test_idshape_backstop.py`, `tests/test_number_words_and_amounts.py`) |
| A1 | An external tester's set: 1 of 3 postal-address cases reached the provider. Addresses were only a REDACT-and-never-restore variant of `IDENTIFIER`, and the ML detector's `private_address` label had no gateway type | Confirmed | **Fixed in the type system.** `ADDRESS` is a first-class type, data class C1 (a home or postal address is personal data under the DPDP Act): aliased `[ADDRESS_xxxxxx]` and restored exactly in the answer like a name, on every contract whose C1 action is ALIAS and restores C1 (contracts state classes, not types, so each contract's existing C1 line governs it; all shipped contracts were re-versioned, and a contract without a C1 action still fails to load). A type with no data class is redacted, never allowed. Rule-based coverage is deliberately narrow (`pgw.detect.places`): a house marker with street/locality words, a PIN code in an address context, a city or state before a PIN, and adjacent pieces (also across line breaks, up to three more lines) merged into one span; no name or amount rules were added. Free-form addresses depend on the ML detector, whose profile `labels` can now map a model label (e.g. `private_address`) to `ADDRESS`. A multi-line address has one single-line alias and is restored with its original line breaks; a re-wrapped copy elsewhere in the request shares the alias; the egress sweep also checks addresses whitespace-normalised. Record-structured text containing addresses is still refused (§11); RECORDS schemas keep their per-field REMOVE/GENERALIZE dispositions. **Residual:** an address without a house marker, street word or PIN, and in a request without the ML detector, still crosses (the §4 boundary applies). |
| S7 | Medical or harassment details sent | Confirmed (expected, §4) | **Open, by design.** Enforced only at the source |
| C1 | Equal salaries were reported as one person "having the higher CTC" | Confirmed | **Fixed.** A fixed `ranking` sentence says "equal" on a tie |
| C2 | "USD 142,000" was banded as ₹0–5L | Confirmed | **Fixed.** Foreign-currency amounts get no INR band |
| C3/C4 | "Payment of Gratuity Act" aliased as a person; ₹7,000 statutory cap generalised | Confirmed | **Fixed by structural rules** (`tests/test_name_and_legal_consistency.py`). (a) A capitalised run ending in an instrument head word (Act, Rules, Regulations, Code, Scheme, Policy, Ordinance, Amendment, Bill, Order; optional year; "Code on …") is a title: cue and NER PERSON hits in it are dropped and partial-name propagation does not enter it. Roster, declared and ledger hits are never dropped; a name written in front of a title ("Priya's Leave Policy", or a NER name followed by more title words) stays a name. "of" after a capitalised word that does not start a sentence ("Head of Talent Acquisition") is no longer a name cue. (b) An amount governed in its clause by a limit or statutory construction (limit word on its left with no other amount between, "Rs X is the … ceiling", "under the … Act", "as per the Act", prescribed, statutory, notified, minimum wage) is a public constant, unless a specific person anchors it in the same clause (her/his/their, a possessive, a two-word name, "current/expected/offered … salary"): "her salary of Rs 21,00,000 exceeds the ceiling" stays compensation. Contracts may also seal exact `public_constants` (amounts only, at most 8 digits), held out of detection like `org_contacts`. Residual: a person named only in a different clause, or by a single word, does not anchor an amount |
| — | A partial name ("Priya", "Ms Raghunathan", "P. Raghunathan", "priya") got a different alias from the full name | Confirmed (mock provider) | **Fixed.** In a request, a partial mention takes the alias of the one detected or declared full name that contains it, and restores to that full name. Across turns, the restore ledger keeps keyed hashes of each full name's parts linked to its alias (`name_links`), so a later turn that writes only a part gets the same alias (and restores to the part it wrote: the ledger never stores names). **Ambiguity rule:** a part contained in two different full names (two people called Priya, now or earlier in the conversation) is never merged; it gets an alias of its own, stays replaced, and stays ambiguous for the rest of the conversation |
| C5/C6 | Bands shown in drafted emails; honorific guessed from a placeholder ("Mr. Priya") | Confirmed | **Mitigated in guidance.** `hr_email_draft` now tells the model not to copy bands and not to use honorifics. Bands are not restored by design |
| C7 | INSTALL.md said `dev.txt`/`ner.txt` include runtime packages | Confirmed | **Fixed** in INSTALL.md |
| N4 | Re-audit: the identifier backstop masked 6 of 16 fresh harmless texts (ServiceNow INC/CHG/KB, an SAP document, a PO, a Jira key, the company GSTIN), and because `[IDENTIFIER]` was never restored, the answer read "tracked in [IDENTIFIER]" | Confirmed | **Fixed.** (a) IDENTIFIER has its own data class, REF. The four EXT_PSEUDO contracts set `REF: ALIAS` and restore it: the provider sees `[IDENTIFIER_…]`, scoped like any alias, and the reader gets the exact value back, as with names and salaries. A contract that does not state REF gets its C3 action and no restore. (b) A cue-less 12-digit number that passes the Aadhaar checksum is a separate type, AADHAAR_LIKE (C3: removed, never restored). Recognised C3 types are unchanged (decision and rationale in §6). (c) The shipped `business_identifiers` now cover ServiceNow numbering (INC/RITM/REQ/CHG/PRB/PTASK/KB/TASK/SCTASK/CTASK + 7-8 digits), PO/INV/SO/GRN numbers (up to 8 digits, and dated INV-2026-0123 forms) and Jira keys of the HR/IT projects. Two shapes were deliberately **not** exempted: a fully generic Jira key `[A-Z][A-Z0-9]{1,9}-\d{1,6}` is also the shape of employee and member ids (and the loader refuses it), and a bare 10-digit SAP document number cannot be told from an ESIC IP, phone or account number. Both are aliased and restored instead, so the reader still sees them. (d) The loader keeps refusing patterns that match generic personal-identifier samples, and now also refuses a pattern that matches a sample written after its own literal prefix ("PO-" + a 10-digit run). (e) New contract field `org_identifiers`: the organisation's own GSTIN/CIN/TAN as exact strings, exempt from the backstop only (a scheme recognizer still protects a PAN or Aadhaar listed there). (f) Legacy policy purposes (`hris-default@3`) alias IDENTIFIER too; `integration_debug` still removes it. G2 is unchanged (spans are unchanged; only actions changed). Tests: `tests/test_ref_restore_and_instruction_numbers.py`, `tests/test_idshape_backstop.py` |
| N6 | Re-audit: on PATH B, "amit is on 2.45 million rupees" crossed raw although the CTC (₹24,50,000) was a withheld input | Confirmed | **Fixed.** LOCAL_COMPUTE parses every number in the free-text `instruction` to a value (`pgw/product/amounts.py`: plain digits, Indian and Western grouping, lakh/lac/L/LPA, crore/cr, k/thousand, million/mn/m, billion/bn, spelt-out numbers, "point" decimals and mixtures such as "24 lakh 50 thousand"; a bare numeral with two or more significant digits is also tried as thousands, lakh, million and crore). Any number equal to a withheld C2/C3 input, or to a non-RAW output derived from C2/C3 (the aliased difference, internal values), or within ±0.5% of one, is replaced by `[COMPENSATION]`/`[DERIVED]` before the prompt is built, so rounding cannot carry the value. Tests: 23 written forms of fresh values withheld, 8 unrelated numbers kept, an end-to-end PATH B request. Residual in §6.11 |
| — | Evaluation hygiene ("overfitting"): `idshape.GENERIC_SAMPLES` held the auditor's exact ticket values (a passport, a voter ID, a driving licence, a vehicle number, a PF account path) | Confirmed | **Fixed.** The samples are now generated from scheme descriptions with a seeded RNG (checksum-valid Aadhaar, card, GSTIN and IBAN), and `tests/test_eval_hygiene.py` fails if any identifier-like value of `tests/fixtures/audit36.py` appears in `pgw/` (as written or with separators removed). Comment examples that quoted audit amounts were replaced, except comment examples in `pgw/detect/recognizers.py`, a file owned by a concurrent change; the test lets them stay only on `#` comment lines and must be tightened when that change lands. **The audit36 set is a regression fixture only** (its docstring says so): it was read while fixing what it found, so a better score on it is not progress. Progress is measured on held-out data only: the sealed held-out ticket sets and the pre-registered corpora (EVALUATION_PLAN.md) |

### Re-audit (2026-10): control-plane findings

| # | Finding | Status |
|---|---|---|
| N5 | Pointing `PGW_INTEGRITY_MANIFEST` at an older, validly signed tree (NER off with a justification, a 10M-character content limit) started | **Fixed in code (§2).** Monotonic `config_serial` in the signed profile; production refuses a lower serial by the pinned `PGW_MIN_CONFIG_SERIAL` and, independently, by the persisted high-water mark in `PGW_STATE_DIR` (atomic, fsync'd; corrupt or unreadable fails closed). Residual: an attacker who controls both the pinned minimum and the state directory, or holds the signing key (§2) |
| N2 | The "in-house default" private model was an extractive stand-in | **Fixed honestly (§4).** Production refuses the stand-in (identity probe `GET /v1/models`, fixed stand-in id and marker) whenever a contract routes to PRIVATE, and withholds a stand-in reply; development allows it with a loud warning and `X-PGW-Private-Model: standin` on every private reply. No model ships. A real in-boundary model server is an operator obligation, and its answer quality must be evaluated with HR staff |
| N8 | The output gate did not run the identifier backstop: a bare Aadhaar written by the model reached the user | **Fixed.** The output gate (non-stream, stream, private route) also runs `pgw.detect.idshape.detect`. An identifier-shaped token no scheme recognizer typed is masked `[IDENTIFIER]`, unless it lies inside an alias, is a declared organisational value, matches the contract's `business_identifiers`, or is a value this request sent to the model in clear or restores itself (external route) or a declared subject's restorable value such as the requester's employee id (private route) (`tests/test_reaudit_control_plane.py`: `test_identifier_shaped_tokens_the_model_wrote_are_masked[*]`, `test_incremental_stream_masks_identifiers_across_chunk_boundaries`, `test_private_route_masks_identifier_shaped_tokens`). Residual: the backstop's own limits (§6.9: identifiers split across lines, longer than 18 digits, or in words) |
| N9 | Mangled aliases (`[PERSON 2E68HT]`, `[PERSON-8PESJS]`, split by a newline) were shown as-is | **Fixed.** The restorer accepts a space, hyphen or underscore separator, any case, whitespace or a newline inside the brackets, full-width brackets, and one missing bracket, and restores only when the normalised form is exactly a known alias of this request (`alias.LENIENT_ALIAS_RE`). Look-alikes that are not a known alias are left untouched (not marked fabricated). Streaming holds back at most 40 characters of an unclosed bracket that may still become an alias (`test_mangled_alias_of_this_request_is_restored[*]`, `test_lookalike_text_is_left_untouched[*]`, `test_streaming_holdback_for_an_unclosed_bracket_is_bounded`). Residual: other manglings (dropped characters, both brackets missing with a space or hyphen, a type name with spaces) still show as-is |

**What this means.** The audit's main verdict stands. Detection in free text leaks, so free text must not go to an
external model unless its source really reduces it. Run detection as a backstop, not as the control. The changes
above close the side channels the gateway itself was responsible for.

## 8. Held-out evaluation (2026-10-27): progress on data nobody tuned on

After the re-audit showed the previous round had fitted to the auditor's 36 tickets, progress is measured only on
held-out data. `tests/fixtures/audit36.py` is a regression fixture, not a measure.

- **How the set was made.** An independent agent wrote 127 realistic tickets with ground truth: 283 personal
  values and 185 benign strings that must stay readable. It read the docs and contracts but no detection code.
- **How it was kept unseen.** The fixers were forbidden from reading the set. Its scorer reports only aggregate
  counts, never the values. The set was sealed by sha256 before any fix, checked before the run, and run once on
  the final merged code.
- **Disclosed contamination.** Two held-out values, "23rd Jul '91" and "2.45 million rupees", also appear in
  detector comments. Both are examples from the auditor's public re-audit report, which were quoted to the test
  author and to the fixers alike. They are not leaks from the sealed set, and they affect at most 2 of 283 values.

| Measure (127 held-out tickets, production mode, NER on) | Before this round | After |
|---|---|---|
| Tickets leaking any raw value | 47 (37%) | **38 (30%)** |
| Tickets leaking, excluding special-category text | 33 (26%) | **25 (20%)** |
| Raw values leaked | 72 / 283 | **52 / 283** |
| By family: address / money / DOB / people / IDs / special category | 15/16, 16/40, 6/16, 9/102, 7/88, 19/21 | 4/16, 12/40, 5/16, 8/102, 5/88, 18/21 |
| Benign strings over-masked (missing from the restored answer) | 30 / 185 | **0 / 185** |

**Reading.**

- Restorable identifiers removed over-masking entirely.
- Detection of addresses and IDs improved markedly.
- **Leaks remain far from the bar for external processing of real data:** a held-out leak rate near zero on
  structured identifiers, plus a written DPIA. Money in free text (12/40) and dates of birth (5/16) are the
  largest remaining detectable families.
- Special-category text crosses by design whenever input is validly attested (§4). It needs a classifier gate or
  in-house-only purposes.
- **The free-text external route is not safe for real employee data.**


## 9. Transparency report

The gateway can tell the calling app what it did with a request (`pgw_report` v1; schema and examples in
[docs/TRANSPARENCY_REPORT.md](docs/TRANSPARENCY_REPORT.md)). It replaces the development-only trace hook
(`Settings.trace`) as the source of truth for a production UI. The trace hook carries content and production still
refuses it.

**What it reveals, and to whom.** The report goes only to the authenticated app that sent the request, in that
request's own response. It contains:

- the stages that ran, with real measured timings;
- for each protected span: its offsets into the caller's own message strings (on LOCAL_COMPUTE, the payload field
  path instead), its type, class, action, source, and the placeholder that went upstream;
- counts;
- for the external route only, the exact messages that went upstream (protected text), with the sha256 and size
  of the exact body bytes;
- the results of the egress, declared-subject, source and C3 checks.

**Why this is acceptable.** The caller already holds the raw input. Offsets into it, types and placeholders reveal
nothing it does not already have. The outbound messages are its own input after protection, which is less than it
sent. The report holds no restore-ledger value: restore appears only as counts, so no value from an earlier turn or
another conversation can reach it. Every value appears only as offsets into this request's own strings or as a
placeholder.

| Property | Enforced by | Test (`tests/test_transparency_report.py`) |
|---|---|---|
| Opt-in per app in the **signed** registry (`transparency: true`; default false; a non-boolean stops startup). Shipped on for `hr-helpdesk-pilot` only (registry version 3, re-sealed) | `load_registry`, `AppEntry.transparency`, the manifest | `test_transparency_is_a_registry_flag_defaulting_to_false`, `test_shipped_pilot_app_has_it_on_and_the_registry_is_resealed`, `test_editing_the_flag_without_resealing_stops_startup` |
| Truthful. The outbound messages equal what the provider received, and the sha256 and bytes are those of the exact upstream body. `input[start:end]` is the detected value (names, IDs, amounts, declared subjects; multi-message and list-form content; around held-out org contacts). Applying the placeholders at the offsets rebuilds the exact outbound text | `pgw/gateway/report.py` reads the pipeline's own final detections (`Protected.segments/detections`) and the bytes the egress check ran on | `test_non_stream_report_shape_and_outbound_is_exactly_what_went_upstream`, `test_spans_are_offsets_into_the_original_input_with_the_placeholders_that_left`, `test_multi_message_and_list_form_content`, `test_declared_subjects_are_reported_as_declared_at_their_original_offsets`, `test_offsets_stay_exact_around_held_out_organisational_contacts`, `test_stream_report_is_one_event_after_the_content_and_before_done`, `test_local_compute_reports_fields_not_offsets` |
| Never logged, never in the egress ledger, never sent upstream | the report is built only into the response; `_log` is allow-listed; `ledger_line` has fixed fields | `test_report_never_reaches_logs_ledger_or_upstream` |
| No value beyond what the caller sent in this request: no restore-ledger value, nothing from earlier turns or other conversations | restore is reported as counts; spans are offsets | `test_multi_turn_report_holds_no_value_from_earlier_turns_or_other_conversations` |
| No text in error responses. A refusal carries the report inside the error object, with the stages up to the one that stopped. When something was already sent (provider failure, broken stream), `outbound.messages` is null and only the hash and size remain | `Report.build(error=...)` | `test_refusal_reports_stages_up_to_the_stop_and_never_echoes_content`, `test_c4_refusal_stops_at_protect`, `test_early_refusals_after_authentication_carry_a_report[*]`, `test_egress_violation_is_refused_with_the_failed_check_and_no_text`, `test_upstream_failure_after_sending_is_an_error_with_hash_but_no_text`, `test_stream_interrupted_mid_way_reports_an_error_without_text` |
| The in-house route (PRIVATE_LOCAL) never echoes what went in-house. That text is the caller's **unprotected** input, C3 included, and the gateway never returns C3. The report gives the destination, the hash and size, and the model id the in-boundary server reports (`extractive-standin` for the development stand-in) | `Report.build` | `test_private_route_reports_in_house_destination_honestly`, `test_private_route_for_c4_content_after_a_valid_signature`, `test_private_route_names_the_stand_in_as_the_server_reports_it` |
| No report before the app is known (401, pre-auth failures) | the report is created only after `authenticate` | `test_401_and_pre_auth_failures_carry_no_report[*]` |
| Transparency off changes nothing: responses are byte-for-byte those of the gateway before this feature, and no report is built | `report.NULL` (no-op) | `test_transparency_off_matches_the_pre_feature_gateway_bytes`, `test_transparency_off_responses_are_byte_identical`, `test_transparency_off_never_builds_a_report` |
| Bounded: at most 2000 `detected` entries (`truncated: true` past that; counts still cover every span). Building it adds no measurable latency | `report.MAX_DETECTED` | `test_detected_is_capped_and_counts_stay_true`, `test_size_is_bounded_for_a_huge_number_of_spans_and_building_is_fast`, `test_building_the_report_adds_no_measurable_latency` |

**What the report does not do.**

- It does not make the protection better. It shows what detection found, not what detection missed (§8): a value
  that is absent from `detected` was not recognised, and it left raw.
- The `egress_clean` check means only that no value detected or declared **in this request** appears in what left.
  It is not a statement that the text holds no personal data.
- The app is responsible for what it does with the report. Placeholders and offsets are harmless. The outbound
  messages, however, are pseudonymised personal data, as the provider received them, and they need the same
  handling as the input.

## 10. ML layer (defence in depth, not the guarantee)

[docs/ML_ARCHITECTURE.md](docs/ML_ARCHITECTURE.md). The guarantee stays what §1-§4 enforce: mandatory contracts,
structured fields and routing (free text leaves only signed, or stays in-house), the egress check and the output gate.
The ML span detector (`ml_detector`) and the special-category content gate (`content_gate_model`) add detection on
top. What is enforced about them: each model directory is pinned by sha256 in the signed profile (mismatch: startup
stops); inference runs in bounded worker processes and any failure, timeout or full queue fails the request closed
(503, nothing sent); the content gate keeps a request in-house (PRIVATE) or refuses it even when the input is validly
signed. What is NOT enforced: that a model finds what it should. Its recall is a measured property
([docs/ML_EVALUATION.md](docs/ML_EVALUATION.md)), released only through the human-set release gate.

## 11. Record-structured data never crosses on the free-text route

**Why.** Detection is built for prose. A record export pasted into free text (a fixed-width payroll register, a CSV,
an HRIS API payload, a directory dump) puts values in columns or under keys with no sentence context; ALL-CAPS names,
manager names, addresses, PIN codes, rates, leave and medical codes, gender or TDS then cross unrecognised. An
external tester showed both (a fixed-width export sent with two employees' names and pay after the 500 that used to
stop it was fixed; 27/198 values from ten HRMS exports leaked with a valid source signature). A signature says who
produced the text, not that records may leave. Records belong on a typed field-contract route or in-house.

**What is enforced.** On every EXT_PSEUDO purpose, after the source signature is verified (and after the content
gate), `pgw.detect.structure` scans every string of the request (`structure_check` stage and check, §9). If it finds a
record-structured region the request is NOT sent externally:

| contract `structured_content` | result |
|---|---|
| absent, or `{action: REFUSE}` (default) | 422 `structured_data_in_free_text`: fixed message pointing to the field-contract or in-house route, plus `regions` (format family and counts only, never a key or value) |
| `{action: PRIVATE}` | the in-house model (PRIVATE_LOCAL, as for unattested input); with no in-house model configured: refused as above |
| detector error / timeout | 503 `structure_check_unavailable` / `protection_timeout`, nothing sent |

The detector reads shape only (no vendor names, field names or values): delimited and markdown/ASCII tables, space-
aligned and fixed-width tables, repeated record lines with one token-shape signature (`48211 SHANTANU DESHMUKH
41250.00 16500.00` is ID WORD AMOUNT AMOUNT), a lone export row (>= 5 fields, >= 2 amounts, no labels, no more words
than values), key/value blocks (LDIF, YAML lists, INI, `.env`), logfmt lines, JSON (also NDJSON, escaped, truncated)
and XML records. Thresholds: >= 2 rows of >= 3 fields with rows x fields >= 8 (a header row counts as a row);
records in record syntax (JSON/XML/identifier-keyed key/value) from 2 x 3; one record with >= 6 leaf fields in record
syntax or >= 12 fields in a human-written `Label: value` form. Linear time (1 MB in about 2 s worst case; requests are
capped far below that). Header rows of recognised tables are labels: weak detections there are dropped
(`pgw.pipeline._outside`), with no list of header words.

**Measured (dev and synthetic only; nothing held out was read).** Refused share per slice: `eval/data/dev` hr_free_text
0/1593, hris_structured 1110/1110, integration_debug 212/524, negatives 365/750; `tune` hr_free_text 0/535,
hris_structured 356/356; `dev2` hr_free_text 37/620 (all 37 are the `legend_then_rows` genre, which is a table),
hris_structured 420/420; `dev3build` positive 445/720, negative 94/180. Synthetic ticket genres (`ml/train`
programs, 500 each): email threads, chats, letters/forms, OCR forms, address blocks, bank-change forms, salary slips,
Form 16 extracts 0/500 each, BGV forms 1/500; table genres 500/500. The generator in `tests/structured_exports.py`
(22 formats, 2-500 records): 1200/1200 detected.

**The trade-off, stated.** A refused ticket is recoverable (re-send through the field route or in-house); a leaked
export is not, so real exports are refused even when non-personal: invoice, GL, order, inventory and market tables
in the negative slices (~40-50 %), `.env` files, HTTP/log dumps with JSON bodies and raw MIME sources with long
header blocks are refused too. Ordinary tickets (signatures with `Phone: ... / Email: ...`, a two-line salary breakup,
short bullet or numbered lists, dates, chat exports, quoted email headers) are not.

**Not covered.** A sentence that lists several employees' pay ("Ravi's basic is Rs 41,250 and Meera's is Rs 39,100")
is prose: it goes through ordinary detection, which can miss a name. Inline pairs on one line without separators
("SHANTANU DESHMUKH 41250.00, MEERA KULKARNI 39100.00"), a labelled single form below 12 fields, and a table pasted
as a single flattened line are not "structured" either. Multi-turn conversations whose earlier assistant answer is a
table are refused like any other table. LOCAL_COMPUTE purposes take a JSON payload by design and are not checked.

## 12. HRMS records: field contracts (route RECORDS)

[docs/HRMS_CONTRACTS.md](docs/HRMS_CONTRACTS.md). HRMS exports (Workday, UKG/Kronos, Fieldglass, SuccessFactors,
payroll registers, Active Directory) are typed fields with known names; on the free-text path they were flattened
and guessed at, and an independent test measured 14% of personal values leaking with special categories crossing
verbatim. On the RECORDS route values are classified by field, not by detection.

| Property | Enforced by | Tests (`tests/test_hrms_records.py`) |
|---|---|---|
| Every field has a sealed disposition; unknown paths are NEVER (dropped, counted, names reported only to the caller) | `fieldschema.load_schema`, `records._Extractor`; schemas and rules under `registry/schemas`, `registry/rules` sealed by the manifest (all files, used or not) | `test_nothing_but_computed_results_and_aliases_leaves[*]`, `test_tampered_schema_stops_startup`, `test_unsealed_schema_or_rules_file_stops_startup`, `test_tampered_statutory_rule_stops_startup` |
| A schema cannot mark free text KEEP, or a special-category field (by path, name or CSV header word) anything but NEVER | `fieldschema._field` | `test_unsafe_field_schemas_are_refused[*]` |
| NEVER, REMOVE and unselected fields are unavailable to computations; amounts never leave raw (INTERNAL or a sealed band) | `records.bind`, `hrcompute` catalogs | `test_selecting_a_never_or_removed_field_is_refused`, `test_amount_outputs_never_leave_raw` |
| No input value classified other than KEEP is in what leaves, in any surface form; no word of a NEVER value | `RecordResult.check_protected` (prompt with computed tokens held out: Aho-Corasick over withheld strings, numeric comparison over every number, date comparison), then `check_final` (regex + identifier detectors on the final text); violation = 500, nothing sent | `test_nothing_but_computed_results_and_aliases_leaves[*]`, `test_special_category_words_never_leave_even_when_every_row_has_one`, `test_absence_is_counted_without_its_reason` |
| Unsigned or modified payloads are refused like free text | the contract's `external_input` (REFUSE only) | `test_unsigned_record_payload_is_refused`, `test_tampered_payload_after_signing_is_refused` |
| Parsing is bounded and safe: size, rows, depth, CSV shape; XML with any DTD/entity is refused (XXE, billion laughs) | `recparse`, contract `max_bytes`/`max_rows`/`timeout_s` | `test_malformed_payloads_are_400_without_values[*]`, `test_xml_external_entities_dtds_and_bombs_are_refused[*]`, `test_row_and_size_bounds`, `test_large_register_within_time_and_output_bounds` |
| Small groups: no count of 1..k-1 people defined by quasi-identifiers leaves (roster cells; a filter matching 1..k-1 prints "fewer than k" and lists nobody). Complementary suppression continues until no suppressed cell is determined by the printed numbers of the response | `disclosure.Table.protect` (exact interval audit over the response's sums), `records._roster_control`. k comes from the sealed `registry/rules/disclosure_control.yaml`: k < 3 needs a sealed override with a justification, and a missing or modified file stops startup | `tests/test_disclosure_control.py`: `test_a_count_of_one_never_leaves`, `test_complementary_suppression_no_suppressed_cell_is_determined[*]` (an independent brute-force solver over 300 random tables), `test_small_did_not_match_cell_hides_the_total`, `test_tampered_k_stops_startup`, `test_k_below_3_is_refused_even_when_sealed`, `test_missing_disclosure_config_stops_startup` |
| Listed aliases: every printed combination of `show` attributes is shared by at least k people of the population the listing is drawn from (coarsening along sealed hierarchies, then per-row withholding; withheld rows are indistinguishable among at least k). Cost-centre bands are printed only for groups of at least k people | `disclosure.anonymize_listing`, `records._list_control`, `hrcompute.contractor_budget` | `test_listing_rows_meet_k_after_coarsening[*]`, `test_exception_list_with_selected_quasi_identifiers_meets_k`, `test_contractor_cost_centre_bands_need_k_people` |
| Differencing within a conversation: a roster query is refused (422, nothing sent) when its result set differs by 1..k-1 records from an earlier query's result set, or from that set's complement once the total was printed | `disclosure.QueryLog` (filter predicates only; in memory, bounded) | `test_differencing_across_two_queries_in_one_conversation_is_refused`, `test_differencing_against_a_previously_printed_total_is_refused`, `test_query_log_stores_filter_specs_only_and_is_bounded` |

**Computed tokens.** Counts, exception codes, sealed band labels, KEEP enumeration values and generalised values are
the gateway's own validated output. A count can coincide with an input amount (20,000 rows; a Rs 20,000 basic): the
withheld-value check therefore reads the prompt with these tokens held out, and each token is valid by construction
(an integer count, a code from a fixed vocabulary, a label from sealed config). That is the precise claim: no input
value appears in what leaves **outside** a validated computed token.

**Small groups: enforced and not enforced.** Enforced, deterministically, with k from sealed config:
- small-cell suppression, with complementary suppression within one response;
- k-anonymous attributes next to listed aliases;
- refusal of near-duplicate roster queries within one conversation.

Differential privacy was considered and is not built; docs/HRMS_CONTRACTS.md "Small groups" gives the reasons.

**Not enforced** (small groups):
- Inference from the LLM's background knowledge. A provider that already knows who is in a group of k learns that
  group's printed attribute.
- Attribute disclosure when all k people of a group share the same value (homogeneity). l-diversity and t-closeness
  are not built.
- Differencing across conversations, since a caller can rotate `X-PGW-Conversation-Id`.
- Differencing across gateway processes or restarts, since the log is in memory.
- Differencing across two different exports, since no input is stored.
- Differencing through `group_by` cells of overlapping filters, since only matched sets are compared.
- Reasoning about the suppression algorithm itself.
- The per-person exception codes of payroll, timecard and contractor lists. They are about the aliased person by
  design and carry no quasi-identifier unless the contract shows one.

**Not enforced** (other). That the schema names a vendor field correctly: a misnamed special category under a field
mapped KEEP would leave, so review schemas like code (unknown fields are safe). That the starter statutory values are
current (they are sealed config with sources
cited; verify them). The payload is parsed on the gateway host: the caller's JSON body and the attestation digest are
computed on the event loop (bounded by `max_bytes`).

## 13. Plugins: outside libraries side by side, never replacing

[docs/PLUGINS.md](docs/PLUGINS.md). Mature open-source components (python-stdnum, phonenumbers, detect-secrets) sit
behind two plugin slots (validator, recognizer). **Enforced:** modes come only from the signed deployment profile
(`plugins:`); an unknown plugin or mode stops startup; every plugin is off by default; a shadow plugin cannot change
what leaves or the report (it only adds counts to operator metrics, never values) and its errors are ignored and
counted; a union-mode recognizer can only ADD masking (no character the built-in masks is released) and its failure
fails the request closed (503); the egress check covers union-mode values like any detection; adapters do no network
or file I/O. **Not offered:** an `enforce` mode for validators. Which values count as an Aadhaar, a PAN or a card stays
the built-in's decision until humans compare both on held-out data and change it in a reviewed, signed profile.
**Not enforced:** that a plugin finds anything (measured by scripts/compare_validators.py and held-out runs).
