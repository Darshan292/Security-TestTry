# Operations

Operational controls around the privacy gateway that code alone does not enforce.

## Contract ownership and change process

- **Owners.** Policy packs (`policies/`), data contracts (`contracts/`), the app registry (`registry/`), `pgw/product/` and `pgw/egress.py` decide what may leave the organisation. They are owned by the security-owner team named in `.github/CODEOWNERS` (currently the placeholder `@ESU-ORG/privacy-gateway-owners`; replace it with the real team).
- **Review.** Every change to those paths needs approval from a code owner. Enable "Require review from Code Owners" in branch protection on the default branch; CODEOWNERS has no effect without it.
- **Version bump rule.** Any change to a policy or contract file must increase its `version:` field in the same change. The manifest records version and hash, and the gateway refuses a file whose version differs from the sealed one.
- **Reseal after review.** After approval, run `python -m pgw.product.integrity seal` to rewrite `policies/MANIFEST.json` and commit it in the same change. The seal covers `policies/*.yaml` and `contracts/*.yaml`.
- **Signing.** To sign the manifest: `PGW_POLICY_SIGNING_KEY=<hex> python -m pgw.product.integrity seal` (HMAC-SHA256 over the file hashes). The deployed gateway needs the same `PGW_POLICY_SIGNING_KEY` to verify, and `PGW_REQUIRE_SIGNED_POLICY=1` makes an unsigned manifest a startup error.
- **Signing key custody.** Hold the key in a secrets manager. Give the sealer role (release owners only) read access to seal, and give the gateway runtime read access to verify. Do not store it in the repository, CI logs or the image. Note the HMAC is symmetric: anyone who can verify can also sign, so keep the signing key out of reach of people who merely deploy. Rotate it on personnel change by resealing with a new key and redeploying the gateway with it. Decide key owner and rotation schedule: TBD by the organisation.
- **Unsealed changes are refused.** At startup the gateway rejects any policy or contract that is missing from the manifest, differs from its sealed hash, or has a different version. CI runs the same check on every push and PR (`.github/workflows/ci.yml`).
- Registry changes (`registry/apps.yaml`) are not covered by the manifest; they rely on CODEOWNERS review alone.

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
