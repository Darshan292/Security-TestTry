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
- **Unsealed changes are refused.** At startup the gateway rejects any registry, policy or contract file that is missing from the manifest, differs from its sealed hash, or has a different version. Examples: an added purpose or model, a rebound contract, a replaced key hash. CI runs the same check on every push and PR (`.github/workflows/ci.yml`).
- **Every purpose needs a contract.** A registry purpose without a data contract stops startup. The pre-contract ("legacy") path is development-only and cannot be enabled in production (SECURITY_BOUNDARY.md §1).
- **One purpose, one contract.** Contracts are resolved globally by purpose. Two registry entries that bind one purpose to files with different content stop startup, even when the name and version strings match. Tenant-specific contracts are not supported.
- **Source-side input boundary.** Each contract lists the source-side sanitizers or exports (`external_input`) whose free text it accepts. The app team that owns the sanitizer is responsible for it removing NOT_EXPECTED attributes. The gateway records the attestation; it does not verify it (SECURITY_BOUNDARY.md §4).

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
