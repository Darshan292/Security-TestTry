# Operations

Operational controls around the privacy gateway that code alone does not enforce.

## Contract ownership and change process

- **Owners.** Policy packs (`policies/`), data contracts (`contracts/`), the app registry (`registry/`), `pgw/product/` and `pgw/egress.py` decide what may leave the organisation. They are owned by the security-owner team named in `.github/CODEOWNERS` (currently the placeholder `@ESU-ORG/privacy-gateway-owners`; replace it with the real team).
- **Review.** Every change to those paths needs approval from a code owner. Enable "Require review from Code Owners" in branch protection on the default branch; CODEOWNERS has no effect without it.
- **Version bump rule.** Any change to the registry, a policy or a contract file must increase its `version:` field in the same change. The manifest records version and hash, and the gateway refuses a file whose version differs from the sealed one.
- **Reseal and sign after review.** After approval, the approver runs `python -m pgw.product.integrity seal --sign-key KEYFILE` to rewrite `policies/MANIFEST.json` with an Ed25519 signature, and commits it in the same change. The seal covers `registry/*.yaml` (including the deployment profile `registry/deployment.yaml`), `policies/*.yaml`, `contracts/*.yaml` and `roster/*.jsonl`.
- **Deployment settings are configuration too.** In production every security-relevant setting (NER model and workers, egress allowlist and provider URLs, private endpoint and allowlist, provider-terms attestation and region, body/content/message limits, protection and upstream timeouts, concurrency, the sealed roster/registry/policy paths) is read from the signed `registry/deployment.yaml`. **Changing a limit = edit the profile, bump its `version`, get code-owner review, re-seal and sign, redeploy.** Setting a different value in the environment does not change it: startup stops and names the setting. Turning NER off for contracted purposes needs `ner_model: null` plus a written `ner_off_justification` (at least 20 characters) in the same reviewed, signed change. Secrets, key files, the trust anchor, `PGW_MODE` and log/ledger locations stay in the environment (deploy/README.md).
- **Production requires the signature.** In `PGW_MODE=production` (the default) the gateway starts only if the manifest's Ed25519 signature verifies under `PGW_TRUSTED_CONFIG_PUBKEY`. The manifest committed in this repository is unsigned, so it is valid for development only (SECURITY_BOUNDARY.md §2). The older HMAC (`PGW_POLICY_SIGNING_KEY`, `PGW_REQUIRE_SIGNED_POLICY=1`) is still verified if set, but it is symmetric: whoever can verify can also sign, so it is not a trust anchor.
- **Signing key custody.** Create the key once with `python -m pgw.product.integrity keygen KEYFILE`. The private key stays offline with the release approvers: not in the repository, CI, the image or the gateway's environment. The gateway gets only the public key. Supply that public key from configuration the config authors cannot write (secret store, image, admission policy), because whoever can replace it can make the gateway trust their own signature. Rotate the key on personnel change: re-seal with a new key and redeploy with the new public key. Key owner and rotation schedule are TBD by the organisation.
- **Configuration serial (rollback protection).** `registry/deployment.yaml` carries `config_serial`, a monotonic integer. **Every signed configuration change raises it** (any sealed file, not only the profile), in the same reviewed change. Production refuses to start when the serial is below `PGW_MIN_CONFIG_SERIAL` or below the high-water mark `config_serial.json` in `PGW_STATE_DIR` (SECURITY_BOUNDARY.md §2); both are required.
  - `PGW_MIN_CONFIG_SERIAL` has the same custody as `PGW_TRUSTED_CONFIG_PUBKEY` (secret store, image, admission policy; not writable by config authors). Set it to the serial being deployed with each release, so the floor also holds on a new host or a lost state directory.
  - `PGW_STATE_DIR` is an existing directory on persistent local disk (the compose file uses the `/var/lib/pgw` volume). The gateway does not create it. It records there the highest serial it has started with (atomic write with fsync, under a lock) and refuses anything lower. Back it up with the replay store.
  - **Corrupt or unreadable high-water mark:** startup stops and the file is left as it is; the gateway never resets it. Investigate first (it may be tampering). Then restore the file from backup, or re-create it with the serial of the configuration last deployed: `{"config_serial": N, "kind": "pgw-config-high-water"}`.
  - **Deliberate rollback** (returning to an older configuration): do not point the gateway at the old signed tree; it is refused. Take the old content, set `config_serial` above the current one, review, re-seal and sign it as a new change, raise `PGW_MIN_CONFIG_SERIAL` to that serial and deploy. Lowering the minimum and deleting the high-water mark would also work, which is why both are kept under separate custody.
- **Unsealed changes are refused.** At startup the gateway rejects any registry, policy or contract file that is missing from the manifest, differs from its sealed hash, or has a different version. Examples: an added purpose or model, a rebound contract, a replaced key hash. CI runs the same check on every push and PR (`.github/workflows/ci.yml`).
- **Every purpose needs a contract.** A registry purpose without a data contract stops startup. The pre-contract ("legacy") path is development-only and cannot be enabled in production (SECURITY_BOUNDARY.md §1).
- **One purpose, one contract.** Contracts are resolved globally by purpose. Two registry entries that bind one purpose to files with different content stop startup, even when the name and version strings match. Tenant-specific contracts are not supported.
- **Source-side input boundary.** Each contract lists the source-side sanitizers or exports (`external_input`) whose free text it accepts. The app team that owns the sanitizer is responsible for it removing NOT_EXPECTED attributes. The gateway records the attestation; it does not verify it (SECURITY_BOUNDARY.md §4).

## Source signing keys (input attestation)

Free text reaches an external model only when the source system that produced it signs it (SECURITY_BOUNDARY.md §4).
Each contract's `external_input.sanitizers` maps a source id to that source's Ed25519 public key.

- **Demo keys must be replaced.** The repository's contracts ship with DEMO public keys whose private halves are
  committed in `tests/fixtures/DEMO_ONLY_source_signing_keys.json`. A production gateway refuses to start while any
  contract carries one (`pgw.product.attest.DEMO_PUBKEYS`). Development mode accepts them, with a warning.
- **Creating a key.** The source system's owner runs `python -m pgw.product.attest keygen KEYFILE` (file mode 0600,
  never overwritten). The private key stays in the source system: not in the calling app, the gateway, the
  repository or CI. Put the printed public key in the contract (quoted), bump the contract `version`, and reseal and
  sign the config as for any contract change.
- **Signing.** The source signs each request for the calling app's id with `pgw.product.attest.sign` (or an
  equivalent implementation of `canonical_message`, with a fresh random nonce per request). The calling app forwards
  the four headers unchanged. Each signature is accepted once: a retry needs a new signature. The app must not hold the key: an app
  that can sign can attest anything, and the guarantee reduces to the old unverified claim.
- **Replay store.** Production requires `PGW_REPLAY_DB`: a SQLite file on local persistent storage that every
  gateway process on the host shares. Back it up like the restore ledger and monitor its disk.
  - **On failure:** a store error answers 503 (`replay_store_unavailable`) and sends nothing. Alert on
    `attestation_replay_store_unavailable`.
  - **When full:** a store with 100 000 live entries answers 503 (`gateway_busy`) and logs
    `attestation_replay_cache_full`.
  - **Several hosts:** don't share the SQLite file over NFS. A shared database backend is still to be built
    (SECURITY_BOUNDARY.md §4).
- **Clocks.** The signed timestamp must be within ±300 s of the gateway clock (`MAX_SKEW_S`). Run NTP on both sides.
  Refusals carry a content-free `attestation` reason (`stale_timestamp`, `bad_signature`, ...), also written to the
  ledger as `input_attestation`.
- **Rotation.** Add the new key under a new source id (for example `helpdesk-ticket-export@2`) next to the old one,
  re-sign and deploy, move the source to the new key, then remove the old entry and re-sign again. A contract may
  list up to 10 sources. A key cannot be revoked faster than a signed config redeploy.
- **Compromise.** Remove or replace the source's entry and redeploy the signed config. Until then, whoever holds the
  key can send any free text to the external model for that purpose. Owners, rotation schedule and the compromise
  procedure are TBD by the organisation.
- **In-boundary fallback.** The shipped EXT_PSEUDO contracts set `unattested: PRIVATE`, so the gateway needs a
  configured private endpoint to start. A deployment without one sets `unattested: REFUSE` and re-signs.

## In-boundary model (PRIVATE route)

Contracts that keep unattested input or C4 content in-boundary (`unattested: PRIVATE`, `C4: PRIVATE`; the shipped EXT_PSEUDO contracts do) need a real language model inside the trust boundary.

- **What ships is not a model.** `pgw/private_llm.py` without `PGW_PRIVATE_GGUF` (and `llama-cpp-python`) is an extractive stand-in that returns the ticket's first sentences. It always identifies itself as `extractive-standin` (`pgw_backend: standin`) on `GET /v1/models` and on every reply. No model weights are in this repository.
- **Production refuses it.** At startup the gateway probes the private endpoint with `GET /v1/models` and stops if it is the stand-in, cannot be reached or answers with something other than a model list. A stand-in reply seen later (endpoint swapped) is withheld with 503 `private_model_unavailable`; alert on `private_model_standin_reply_refused`. In development the stand-in is allowed with a loud startup warning, and every private-route reply carries `X-PGW-Private-Model: standin`.
- **What a real deployment needs:**
  - an OpenAI-compatible server inside the boundary, e.g. vLLM (`vllm serve <model>`), Ollama (`/v1` API) or the llama.cpp server, with `/v1/chat/completions` and `/v1/models`, reachable only from the gateway, at a host in the signed `private_allowlist`, configured as `private_upstream_url` in the signed profile;
  - the model server started and ready before the gateway: the gateway probes it once at startup and does not start while the probe fails (let the restart policy retry);
  - `PGW_PRIVATE_MODEL` set to a model id that endpoint lists (otherwise a `private_model_not_listed_by_endpoint` warning, and the server will likely reject the requests);
  - capacity planning (the private route is not streamed and waits for the whole answer, within `upstream_timeout_s`), logging on the model server that keeps no prompts, and the same host hardening as the gateway, since it receives unprotected personal content;
  - **an answer-quality evaluation with HR staff** on synthetic tickets of the relevant purposes and languages before the route is relied on. The gateway's tests prove routing, isolation and fail-closed behaviour only; nothing here measures whether the model's answers are correct or useful.
- **Without such a model**, set `unattested: REFUSE` (and `C4: REFUSE`) in the contracts and re-sign: unattested or unreadable input is then refused instead of answered in-boundary.

## Provider data retention and region

The gateway minimises what is sent; it does not control what a provider does with it. Before enabling a provider for real data, the organisation must have, per provider and per account or workspace used:

- A contract or enterprise terms that state **zero (or minimal, specified) data retention** for API inputs and outputs, including logs and abuse-monitoring storage.
- Terms that state prompts and outputs are **not used for model training**.
- A confirmed **processing and storage region** that meets the organisation's data residency obligations, and a sub-processor list the DPO has reviewed.
- A named owner for re-checking these terms when they change.

Anthropic and Groq: verify with the provider's current terms and agreements (retention, training use, regions, available zero-data-retention arrangements). This document does not state them because they change and depend on the contract in force.

**Region is not enforced by the gateway.** The egress allowlist (`PGW_EGRESS_ALLOWLIST`) restricts destination hostnames only. It does not choose or verify a region, so region compliance is an organisational control: configure the provider account/endpoint accordingly and record the evidence.

## Audit storage

The egress ledger (`PGW_EGRESS_LEDGER`, JSONL) has one row per upstream call or block: request id, tenant, app, purpose, model, provider, body hash, entity-type counts, policy/integrity refs, decision, latency. Rows never hold request or response text or values.

- **Tamper evidence** (`pgw/audit.py`). Every row carries `seq` (1, 2, ...) and `prev` = SHA-256 of the previous row's exact bytes (first row: 64 zeros). When a tenant key file is configured, each row also carries `kid` and `mac` = HMAC-SHA256 with the platform audit key (HKDF of the platform key, purpose `audit`), so the chain cannot be rebuilt without the key and MACs cannot be stripped. Each append is `fsync`'d.
- **Fail closed.** At startup the gateway verifies the whole existing log and refuses to start if any row is edited, deleted, inserted, reordered or torn. While running, if the file size changes outside the writer, or a write fails, the writer stops and every request gets 503 `ledger_unavailable` (nothing is sent).
- **Verify:** `python -m pgw.audit verify <path> --keys-file <tenant keys>` (exit 0 = intact, 1 = broken, with the first bad line number and no row content). Use `--no-mac` to check the hash chain only. Run it on a schedule and before handing a log to an investigator.
- **Recovery after a failed check.** Do not edit the log. Move it aside as evidence, investigate, and start a new log file; record the old head hash (`verify` prints it) in the incident.
- **Upgrade note.** A log written before this change has no chain and will not verify; archive it and start a new file.
- **Remaining (production):** ship every row to WORM / remote append-only storage (object lock, a log service with retention lock) and anchor the head hash there periodically. The local file alone cannot detect truncation of the newest rows or a rollback to an older copy of itself, and a host-level attacker holding the platform key can rewrite it. Retention period and owner: TBD by the organisation.

## Key management

Aliases, the restore ledger and the audit MAC are keyed with HKDF-SHA256 (`info = pgw/{purpose}/v1/{tenant}`).

- **Per-tenant keys** (`pgw/keys.py`, `PGW_TENANT_KEYS_FILE`). A JSON/YAML file, mode 0600 (the gateway refuses a file readable by group/others), holding an independent random master per tenant and per key id, plus a separate **platform** key (tenant `*`) used only for tenant-neutral things: the roster EDM index, the audit-chain MAC, and restore-ledger rows written before key ids existed. Compromise of one tenant's key does not expose another tenant's aliases or ledger hashes.
- **Fail closed.** A request whose tenant has no entry gets 503 and nothing is sent; there is no fallback to the platform key or another tenant's key. Startup logs `tenant_keys_missing` (a count, no names) if registered tenants lack keys.
- **Single master key** (`PGW_MASTER_KEY`) still works when no key file is set (tests, demos): every tenant derives from the one master with key id `v1`, as before. The audit chain is then hash-chained but not MAC'd. Migrating: `python -m pgw.keys init --file F --import-master-from-env` makes the old master the platform key, so in-flight restore-ledger rows stay valid.
- **Rotation.** `python -m pgw.keys rotate --tenant acme --file F` adds the next key id (`k2`, ...) and makes it active (`--tenant '*'` rotates the platform key). The file is replaced atomically (temp file 0600, fsync, rename); no key material is printed. Restart the gateway to load it. Effects: new conversations and new restore-ledger rows use the active id; each conversation's alias key id is pinned in the restore ledger, so an existing conversation keeps stable aliases on its old key id; ledger rows store their key id and are still matched until they expire (24 h TTL).
- **Retirement.** `python -m pgw.keys retire --tenant acme --key-id k1` removes a non-active id. Do it only after the ledger TTL has passed since rotation: a live ledger row under a removed id makes that conversation's requests fail closed (503). Conversations pinned to a removed id move to the active id (their aliases change once). Keep retired platform key ids that are needed to verify old audit MACs, or verify and archive those logs first.
- `python -m pgw.keys list` shows tenants and key ids only.
- **Remaining (production):** keep tenant masters in a KMS/HSM (envelope-encrypt the file, or derive inside the KMS) instead of a plaintext file on the host; separate the platform audit key from the runtime host so the gateway cannot forge history; define rotation schedule, owners and compromise procedure (rotate, retire after TTL, erase affected conversations): TBD by the organisation. Hot reload of keys is not implemented.
