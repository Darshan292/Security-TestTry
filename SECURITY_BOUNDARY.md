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

The registry, the policy pack and every data contract are sealed in `policies/MANIFEST.json`, with the sha256 and
version of each file.

| Property | Enforced by | Test |
|---|---|---|
| The registry (`registry/*.yaml`) is sealed with the policies and contracts. Adding a purpose or a model, rebinding a task, or replacing a key hash without re-sealing stops startup | `integrity.SEALED_GLOBS`, `_sealed_paths` | `test_unreviewed_change_stops_startup[registry *]` |
| Production requires an **Ed25519** signature over the manifest that verifies under `PGW_TRUSTED_CONFIG_PUBKEY`. The following are all refused: a missing key, an unsigned manifest, an attacker's re-seal (unsigned or under another key), and a manifest edited after signing | `integrity.verify(require_trusted=True)` | `test_resealing_without_the_trusted_key_does_not_help`, `test_repo_manifest_seals_the_shipped_registry_policy_and_contracts` |
| Unsigned configuration and a missing manifest are accepted only in development | `check_startup_mode` | `test_unsigned_config_is_development_only` |

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

Free text reaches an external model only if it comes from a source that reduces it. Each contract must declare:

```yaml
external_input:
  sanitizers: [helpdesk-ticket-export@1]   # source-side exports/sanitizers this purpose accepts
  unattested: REFUSE                        # or PRIVATE (EXT_PSEUDO contracts with an in-boundary model)
```

Every request for a contracted purpose must carry `X-PGW-Input-Source: <one of those ids>`, on every route. That
includes LOCAL_COMPUTE, whose `instruction` field is free text that goes to the external model.

- If the header is missing, empty, unknown or names another contract's sanitizer, the input is not sent externally.
  With `REFUSE`, the response is 422 `input_not_source_sanitized`, nothing is sent, and the ledger records
  `BLOCKED` with reason `input_not_sanitized`. With `PRIVATE`, the input goes only to the in-boundary model and the
  ledger records `SENT_PRIVATE` with the same reason.
- An accepted sanitizer id is recorded on the ledger line (`input_source`).
- A contract without a valid `external_input` does not load. `PRIVATE` is allowed only on EXT_PSEUDO contracts, and
  only when a private endpoint is configured.
- Tests: `test_unattested_input_is_refused_before_any_send`, `test_local_compute_free_text_needs_the_attestation_too`,
  `test_unattested_input_can_be_kept_in_boundary`, `test_contracts_must_declare_a_valid_input_boundary`,
  `test_every_shipped_contract_declares_its_input_boundary`.

### What the attestation is, and what it is not

The header is a **declaration by the authenticated calling app**: "this text came out of our sanitizer or export X".

- **What the gateway checks:** that the app made the declaration, that the purpose allowlists it, and that it is
  recorded on the ledger line.
- **What the gateway does not check:** whether sanitizer X exists, whether it ran, or whether it removed anything.

The NOT_EXPECTED guarantee therefore rests on the source system. The candidates are:

- an allowlisted export of HRIS fields;
- a ticket form without free-text medical fields;
- a reviewed sanitizer owned by the app team.

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
2. **The truth of `X-PGW-Input-Source`.** It is an accountable claim, not a verified fact.
3. **The trust anchor.** The gateway cannot verify where `PGW_TRUSTED_CONFIG_PUBKEY` came from, or whether
   `PGW_MODE=development` is used outside development.
4. **Network-level egress.** The app must not be able to reach the provider around the gateway. That is the
   network policy in `deploy/`, not application code.
5. **Provider behaviour.** Retention and region are attested by the operator (`PGW_PROVIDER_*`), not verified.
6. **Tenant-specific policy.** Not supported (§3).
7. **Declared subjects' completeness.** People the app does not declare rely on detection.
