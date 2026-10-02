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

See implementation in this phase. (Placeholder: to be filled in by the audit-storage owner.)

## Key management

See implementation in this phase. (Placeholder: to be filled in by the key-management owner.)
