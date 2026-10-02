# Deployment: apps cannot bypass the gateway

Addresses THREAT_MODEL T13 (apps call providers directly) and ADR-0001 rule 1 (only the gateway holds provider keys and only gateway pods reach providers). Three independent layers; each should hold even if another fails.

1. **Network policy (`docker-compose.yml`, `k8s/networkpolicy.yaml`).** App containers sit on an `internal: true` network (compose) or under a default-deny egress policy that allows only the gateway service and DNS (Kubernetes). The gateway alone is attached to an egress network / allowed to reach provider endpoints. In Kubernetes, hostname-level egress control needs Cilium/Calico FQDN policy or an egress proxy; plain NetworkPolicy only offers CIDRs, which are coarse and go stale (the CIDR in the file is a placeholder to verify).
2. **Only the gateway holds provider keys.** `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are injected only into the gateway service; apps get a gateway URL and (optionally) a per-app gateway key. An app that opens a route anyway has no credential to use. Enforce in review and secret scanning: no provider keys in app repos or app namespaces.
3. **Gateway destination allowlist (in code).** `pgw.gateway.settings.check_destination(url, allowlist)` raises `ValueError` unless the URL is https and its exact hostname (and port, 443 unless the entry says otherwise) is listed. The list comes from `Settings.egress_allowlist` / `PGW_EGRESS_ALLOWLIST`. This stops a misconfigured or tampered upstream URL from sending data elsewhere, but it does not stop an app that bypasses the gateway process; that is layers 1 and 2.

**Honest status.** Layer 3 is code with unit tests (`tests/test_egress_allowlist.py`). Layers 1 and 2 are configuration that has **not been exercised** in this repository's environment: no Docker daemon or Kubernetes cluster with an enforcing CNI was available, so the compose file and policies are unvalidated beyond review. Before relying on them, verify on a real deployment:

- compose: `docker compose run --rm example-app sh -c 'curl -m5 https://api.anthropic.com'` must fail, while the same from the gateway container succeeds;
- Kubernetes: from an app pod, `curl -m5 https://api.anthropic.com` must time out and `curl gateway.pgw:8080/healthz` must work (a `kubectl exec` test belongs in the T13 "network policy test" review item).

## Gateway settings that matter for a pilot (all fail closed)

| Env | Default | Effect |
|---|---|---|
| `PGW_EGRESS_ALLOWLIST` | `api.anthropic.com` | Exact https hosts a real upstream may use; checked at startup |
| `PGW_ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | Passed to the SDK explicitly, so the SDK's own `ANTHROPIC_BASE_URL` cannot redirect egress |
| `PGW_INTEGRITY_MANIFEST` | `policies/MANIFEST.json` | Policy and contracts must match their sealed sha256 and version, or the gateway does not start. No env switch turns this off |
| `PGW_POLICY_SIGNING_KEY` / `PGW_REQUIRE_SIGNED_POLICY=1` | unset / off | HMAC-signed manifest. Keep the key out of the app namespaces |
| `PGW_NER_MODEL` | `en_core_web_lg` | Local NER on contracted purposes. A model that does not load stops startup; `none` disables it (logged as a warning) |
| `PGW_MAX_BODY_BYTES` / `PGW_MAX_CONTENT_CHARS` / `PGW_MAX_MESSAGES` | 262144 / 50000 / 200 | 413, nothing sent |
| `PGW_PROTECT_TIMEOUT_S` / `PGW_MAX_CONCURRENT_PROTECT` | 10 / 8 | 503 on deadline or when all worker slots are busy. A timed-out worker keeps its slot until it really ends |
| `PGW_UPSTREAM_TIMEOUT_S` | 60 | Provider timeout; failure is 502 with no content |
