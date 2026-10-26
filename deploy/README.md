# Deployment: apps cannot bypass the gateway

Addresses THREAT_MODEL T13 (apps call providers directly) and ADR-0001 rule 1 (only the gateway holds provider keys and only gateway pods reach providers). Three independent layers; each should hold even if another fails.

1. **Network policy (`docker-compose.yml`, `k8s/networkpolicy.yaml`).** App containers sit on an `internal: true` network (compose) or under a default-deny egress policy that allows only the gateway service and DNS (Kubernetes). The gateway alone is attached to an egress network / allowed to reach provider endpoints. In Kubernetes, hostname-level egress control needs Cilium/Calico FQDN policy or an egress proxy; plain NetworkPolicy only offers CIDRs, which are coarse and go stale (the CIDR in the file is a placeholder to verify).
2. **Only the gateway holds provider keys.** `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are injected only into the gateway service; apps get a gateway URL and (optionally) a per-app gateway key. An app that opens a route anyway has no credential to use. Enforce in review and secret scanning: no provider keys in app repos or app namespaces.
3. **Gateway destination allowlist (in code).** `pgw.gateway.settings.check_destination(url, allowlist)` raises `ValueError` unless the URL is https and its exact hostname (and port, 443 unless the entry says otherwise) is listed. In production the list comes from `egress_allowlist` in the signed deployment profile `registry/deployment.yaml` (below); `PGW_EGRESS_ALLOWLIST` may only repeat it. This stops a misconfigured or tampered upstream URL from sending data elsewhere, but it does not stop an app that bypasses the gateway process; that is layers 1 and 2.

**Honest status.** Layer 3 is code with unit tests (`tests/test_egress_allowlist.py`). Layer 1 for **compose** was exercised on 2026-10-02 (see "Verified 2026-10-02" below); the **Kubernetes** policy remains **unexercised** (no cluster/CNI), and layer 2 is configuration only (not tested). Before relying on them, verify on a real deployment:

- compose: `docker compose run --rm example-app sh -c 'curl -m5 https://api.anthropic.com'` must fail, while the same from the gateway container succeeds;
- Kubernetes: from an app pod, `curl -m5 https://api.anthropic.com` must time out and `curl gateway.pgw:8080/healthz` must work (a `kubectl exec` test belongs in the T13 "network policy test" review item).

## Gateway settings that matter for a pilot (all fail closed)

**In production, security-relevant settings come from the signed deployment profile, not the environment.**
`registry/deployment.yaml` is sealed in `policies/MANIFEST.json` (the `registry/*.yaml` glob), so it is
Ed25519-signed with the registry, policy and contracts. It must state every security-relevant setting: the sealed
registry/policy/roster files, provider kind and URLs, the egress and private allowlists, the provider-terms
attestation and region, NER model and workers, and every limit and timeout. The full classification of every setting
is the table in `pgw/gateway/settings.py`.

- An environment variable (or Settings value) that differs from the profile **stops startup**. The message names
  the setting and the variable, never the value. An unset variable, or one equal to the profile's value, is fine.
  The simplest deployment sets none of them.
- The profile is found next to the manifest (`<manifest dir>/../registry/deployment.yaml`); no variable points
  elsewhere. A missing profile, an edited one, or one missing from the signed manifest stops production.
- **NER off is a signed decision.** `ner_model: null` starts in production only if the profile also gives
  `ner_off_justification` (at least 20 characters). A model that does not load still stops startup.
- **Development** (`PGW_MODE=development`): the profile is optional; differences are logged as
  `deployment_profile_differs_development_only` warnings and the environment value is used.
- Changing any of these values = edit the profile, bump its `version`, review, re-seal and sign (OPERATIONS.md).

The shipped profile holds the defaults below (provider `mock`, no provider attestation). A real deployment edits
it (e.g. `upstream_kind: anthropic`, `provider_zero_retention: true`, `provider_region`) in a reviewed, signed change.

| Profile setting (env that may only repeat it) | Shipped value | Effect |
|---|---|---|
| `egress_allowlist` (`PGW_EGRESS_ALLOWLIST`) | `[api.anthropic.com]` | Exact https hosts a real upstream may use; checked at startup |
| `anthropic_base_url` / `upstream_url` (`PGW_ANTHROPIC_BASE_URL` / `PGW_UPSTREAM_URL`) | `https://api.anthropic.com` / `https://api.openai.com/v1` | Passed to the SDK explicitly, so the SDK's own `ANTHROPIC_BASE_URL` cannot redirect egress |
| `upstream_kind` (`PGW_UPSTREAM`) | `mock` | `openai`/`anthropic` also need `provider_zero_retention: true` and `provider_region` |
| `ner_model`, `ner_workers`, `ner_queue_max`, `ner_timeout_s` (`PGW_NER_*`) | `en_core_web_lg`, 4, 8, 8.0 | Local NER on contracted purposes. A model that does not load stops startup |
| `max_body_bytes` / `max_content_chars` / `max_messages` (`PGW_MAX_*`) | 262144 / 50000 / 200 | 413, nothing sent |
| `protect_timeout_s` / `max_concurrent_protect` | 10 / 8 | 503 on deadline or when all worker slots are busy. A timed-out worker keeps its slot until it really ends |
| `upstream_timeout_s` (`PGW_UPSTREAM_TIMEOUT_S`) | 60 | Provider timeout; failure is 502 with no content |
| `private_upstream_url`, `private_allowlist` | none, loopback + `pgw-private-llm` | In-boundary (C4) endpoint |
| `roster_path` (`PGW_ROSTER`) | none | Sealed roster for name matching; the environment cannot remove it |

Operational settings stay in the environment (they are paths, secrets or the trust anchor, and must not be in the
repo):

| Env | Default | Effect |
|---|---|---|
| `PGW_MODE` | `production` | `production`: signed config and profile required, every purpose contracted, no development switches. `development` is for synthetic-data tests and demos only. The mode is on every audit line and on `/healthz`; check it in deployment review |
| `PGW_REPLAY_DB` | unset (production will not start) | Persistent store of accepted source attestations, so each one is used once. Local persistent disk shared by all gateway processes on the host. Not for network filesystems. A store error refuses requests (503) |
| `PGW_TRUSTED_CONFIG_PUBKEY` | unset (production will not start) | Ed25519 public key that must have signed `policies/MANIFEST.json`. Supply it from a secret store or image the config authors cannot write |
| `PGW_INTEGRITY_MANIFEST` | `policies/MANIFEST.json` | Registry, policy, contracts, roster and deployment profile must match their sealed sha256 and version, or the gateway does not start. No env switch turns this off |
| `PGW_POLICY_SIGNING_KEY` / `PGW_REQUIRE_SIGNED_POLICY=1` | unset / off | Older HMAC check, still verified if set. Not a trust anchor (symmetric key) |
| `PGW_MASTER_KEY` / `PGW_TENANT_KEYS_FILE`, `*_API_KEY` | unset | Secrets |
| `PGW_LEDGER_DB` / `PGW_EGRESS_LEDGER` | local files | Restore ledger and audit log locations |
| `PGW_PRIVATE_MODEL` | `local` | Model name at the in-boundary endpoint |

## Verified 2026-10-02

Scope: compose network topology only (`deploy/verify_network.sh`, run as `./deploy/verify_network.sh`; exits 1 on any FAIL). Real containers on a real Docker 29.3.1 daemon (`docker compose` v5), using `deploy/docker-compose.yml` for networks/attachments plus `deploy/docker-compose.verify.yml` (test-only override: swaps images/commands, no code changes to the real compose file). Dummy credentials only. Result: 13/13 checks PASS.

Setup notes (environment, not product):
- Docker Hub returned 429 (rate limit) in this sandbox; `mirror.gcr.io/library/python:3.12-alpine` pulled fine and was used for both containers.
- The `gateway` here is `python -m http.server 8080`, NOT the real `pgw.gateway` (its image/deps were not built). It proves L3/L4 reachability on `appnet`, not gateway behaviour. Layer 2 (key placement) and layer 3 (allowlist in code) were not part of this test.
- Containers on `egressnet` reach the internet through a TLS-intercepting transparent egress in this sandbox; the gateway therefore needed the sandbox CA (`/root/.ccr/ca-bundle.crt`, mounted at `/ca.crt`, passed to the Python SSL context). On a normal host no CA mount is needed. No proxy env was required inside the container (the sandbox's `127.0.0.1:45333` proxy is not reachable from containers).
- `example.com` answers HTTP 403 from the sandbox egress policy; the check counts any HTTP response as "reachable". `api.anthropic.com` answered 404 for `/` (reachable).

| Check | Command (inside container) | Outcome |
|---|---|---|
| topology | `docker inspect` / `docker network inspect` | example-app: `appnet` only; gateway: `appnet`+`egressnet`; appnet `internal=true`, egressnet `internal=false` |
| 1a/1b/1c app -> https://example.com, https://api.groq.com, https://api.anthropic.com | `urllib.request.urlopen(url, timeout=6)` | PASS (blocked): `URLError <urlopen error [Errno -3] Try again>` (DNS fails) |
| 2 app -> gateway | `urlopen("http://gateway:8080/")` | PASS: HTTP 200 |
| 3a gateway -> https://example.com | same, with CA | PASS (reachable): `HTTP Error 403: Forbidden` from sandbox policy |
| 3b gateway -> https://api.anthropic.com | same, with CA | PASS (reachable): `HTTP Error 404` |
| 4a app -> external IP (172.66.147.243, example.com as resolved by gateway) :443 | `socket.create_connection` | PASS (blocked): `OSError [Errno 101] Network unreachable` |
| 4b app -> 1.1.1.1:443 | same | PASS (blocked): `[Errno 101] Network unreachable` |
| 4c app -> DNS query to 8.8.8.8:53/udp | raw UDP DNS query | PASS (blocked): `[Errno 101] Network unreachable` |
| 4d app resolves example.com | `gethostbyname` | PASS (blocked): `socket.gaierror: [Errno -3] Try again` |
| 4e app with `HTTPS_PROXY=http://<bridge-gw>:45333` | urllib via proxy | PASS (blocked): appnet's host-side gateway 172.18.0.1 `[Errno 111] Connection refused`; 172.19.0.1 `[Errno 101] Network unreachable` |
| 4f app with `HTTPS_PROXY=http://127.0.0.1:45333` | urllib via proxy | PASS (blocked): `[Errno 111] Connection refused` (container loopback) |

Caveat found: the host-side bridge IP of an `internal: true` network (172.18.0.1 here) is still reachable from the app (it answered "refused", not "unreachable"). Any host service bound to `0.0.0.0` (or that bridge IP) is therefore reachable by apps; in this run the sandbox proxy listens only on loopback so the bypass failed, but it is not a general guarantee. Do not run unrelated host services on all interfaces on a gateway host, or add host firewall rules (`DOCKER-USER` / INPUT) for the appnet bridge.

Still unproven: Kubernetes `k8s/networkpolicy.yaml` (no cluster; `kind` not attempted); the real `pgw-gateway` image reaching a real provider with a real key; the in-code allowlist inside a container; behaviour on a Docker host with a different firewall backend.
