# HR Workspace

The HR Workspace is the staff-facing web app for the privacy gateway. HR staff use it to run AI tasks on employee
matters, and it shows them, for every request, what the gateway did with their data.

Code: `pgw/workspace/`. Tests: `tests/test_workspace.py`, which runs the workspace against a real production-mode
gateway built in-process.

## For users

### Tasks

| Task | Input | Where it is answered |
|---|---|---|
| Summarise a ticket | paste the ticket | **kept inside:** the in-house model |
| Explain an HR case | paste the case notes | **kept inside:** the in-house model |
| Draft an email | paste your notes | **kept inside:** the in-house model |
| Compare two salaries | a form: two employees, a fixed instruction | computed inside the gateway; the external AI sees only placeholders and the result |
| ESI eligibility | a form: one employee, monthly gross wage | computed inside the gateway; the external AI sees only placeholders and the result |
| Pay-band position | a form: one employee, annual CTC, band | computed inside the gateway; the external AI sees only placeholders and the result |

**Why pasted text stays inside.** The gateway sends free text to an external AI only if an approved source system
signed it (SECURITY_BOUNDARY.md §4). The workspace cannot vouch for what someone pastes, so it never signs pasted text.
The gateway therefore keeps it with the company's in-house model. The workspace says so before you run, and the
result shows "Kept inside".

**People in this request.** For pasted tasks, list the people the text is about: name, employee ID, email, phone. The
gateway protects each of them wherever they appear, without relying on detection. Each task's contract requires one
role, the requester or the recipient. Without it, the gateway refuses the request, and the workspace tells you what to
add.

**Forms** have typed fields and a fixed list of instructions. Because no free text is involved, the workspace signs
these requests as their source (`hr-workspace-forms@1`). The gateway computes the result from the exact values, and
the external AI never sees the pay.

### Reading a result

- **The journey** (Your request → Privacy shield → Safety check → AI or In-house model → Back to you) replays the
  stages the gateway reported, in order. Each stage is shown for a short minimum time so it can be followed. The times
  printed are the gateway's own measurements, with "Gateway" as their sum and "Round trip" as the time the workspace
  waited. Nothing is simulated. If the gateway did not include a report, the steps are not shown.
- **What you typed / What the AI saw / What you got back.** Coloured chips mark the details the gateway found:
  violet for names, blue for IDs and accounts, amber for pay, rose for dates, teal for contact details, and a striped
  grey chip for withheld values. In "What the AI saw", each detail turns into the placeholder that actually left. In
  "What you got back", the placeholder turns back into the real detail. On the in-house route the gateway does not
  return the text it sent, so that pane shows where the text went (model, size, hash) instead.
- **Outcome:** *Protected* (sent externally, protected), *Kept inside* (in-house model only), *Stopped* (refused;
  nothing was sent), or *Not completed* (an error, for example the gateway could not be reached).
- **What the gateway checked** lists the gateway's own checks, in its own wording. On the in-house route, "source
  verified" fails by design: that is why the text stayed inside.
- **Show technical details** gives the contract, route, request id, stage times, sizes, the SHA-256 of the exact
  outbound body, and the raw outbound messages when the gateway returns them.
- **This session** lists your runs. They are kept in the browser tab's memory only and disappear when you close the
  tab. Nothing is stored on the server.

## For operators

### Architecture

```
browser ──HTTPS──▶ SSO reverse proxy ──▶ hr-workspace (appnet) ──HTTP──▶ gateway ──▶ provider / in-house model
                   (sets X-Forwarded-Email)   holds: app key, form key
```

The workspace is a backend-for-frontend. It serves one HTML page, one script and one stylesheet, and it calls the
gateway's `POST /v1/chat/completions` with its own app key. It has no route to any AI provider.

### Settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `WS_MODE` | `production` | `development` uses a local dev user (`WS_DEV_USER`) and shows a banner on every page |
| `WS_HOST` / `WS_PORT` | `127.0.0.1` / `8090` | listen address |
| `WS_GATEWAY_URL` | required | e.g. `http://gateway:8080` |
| `WS_GATEWAY_KEY_FILE` | required | file holding the workspace's gateway app key (app `hr-workspace`) |
| `WS_SOURCE_KEY_FILE` | unset | Ed25519 private key (hex) for `hr-workspace-forms@1`. If unset, the form tasks are disabled |
| `WS_APP_ID` | `hr-workspace` | the app id the gateway knows the workspace by; it is bound into every form signature |
| `WS_MODEL` | `mock-1` | the model named in requests (must be in the app's `models`) |
| `WS_IDENTITY_HEADER` | `X-Forwarded-Email` | the header your SSO proxy sets |
| `WS_TRUSTED_PROXIES` | required in production | comma-separated IPs/CIDRs of the SSO proxy. Only these peers may assert an identity |
| `WS_PUBLIC_ORIGIN` | required in production | the https origin users open (CSRF check), e.g. `https://hr-workspace.example.com` |
| `WS_GATEWAY_TIMEOUT_S` | `60` | after this the user sees "Gateway did not answer in time" |
| `WS_MAX_BODY_BYTES` | `131072` | request body limit |
| `WS_INTEGRATIONS_FILE` | unset | helpdesk integrations: lines of `<integration-id> <sha256 of its bearer token>` |
| `WS_DEV_USER` | `dev.user@localhost` | development only |

Run it with `python -m pgw.workspace`. A missing or unsafe production setting stops startup with a message naming
it. Production refuses the repository's demo app key (`hr-workspace-key-change-me`) and the demo form-source key.

### Setting up keys (production)

1. **App key.** Generate a random key, store it in the `WS_GATEWAY_KEY_FILE` file, and put its sha256 as
   `key_sha256` of app `hr-workspace` in `registry/apps.yaml`. That entry has `transparency: true`, so the gateway
   returns its report to the workspace.
2. **Form-source key.** Run `python -m pgw.product.attest keygen ws_source_key`. Then, in `salary_comparison.yaml`,
   `esi_letter.yaml` and `band_position.yaml`, replace the demo public key of `hr-workspace-forms@1` with yours.
3. **Re-seal and sign** the configuration (`python -m pgw.product.integrity seal --sign-key …`), and bump
   `config_serial`.

The form key is pinned only on the three LOCAL_COMPUTE contracts. It cannot make pasted text eligible for an
external AI, and a test enforces this.

### Security properties

| Property | How | Test |
|---|---|---|
| Identity only from the SSO proxy | identity header honoured only from `WS_TRUSTED_PROXIES` peers; exactly one value, printable, no comma; everything else is 401. uvicorn runs with `proxy_headers=False` | `test_identity_header_is_required`, `test_identity_from_an_untrusted_peer_is_rejected`, `test_identity_is_the_proxy_header_and_ambiguous_or_bad_values_are_rejected` |
| CSRF | `POST /api/run` needs the exact `Origin`, `X-PGW-Workspace: 1`, a JSON body, and no cross-site `Sec-Fetch-Site`; CORS preflights are never answered | `test_csrf_needs_same_origin_and_custom_header`, `test_csrf_needs_a_json_body_and_no_cors` |
| Strict CSP | `default-src 'none'`, `script-src 'self'`, `style-src 'self'` (no `unsafe-inline`), Trusted Types, `frame-ancestors 'none'`, nosniff, no-referrer, no-store, COOP/CORP, HSTS | `test_strict_csp_without_unsafe_inline_on_every_response`, `test_page_has_no_inline_code_or_external_resources` |
| No XSS sinks | the UI builds the DOM with `createElement` and `textContent` only | `test_frontend_never_parses_data_as_html` |
| Keys stay server-side | app key, form key and integration tokens are never in any response | `test_keys_are_never_in_responses` |
| No content stored or logged | logs carry task, status, outcome, timing, the gateway request id and a 12-hex hash of the user; history lives only in the browser tab | `test_no_content_in_logs`, `test_workspace_persists_nothing` |
| Free text is never signed | sent unattested, so it is kept inside | `test_free_text_is_sent_unattested_and_kept_inside` |
| Forms signed from typed fields only | fixed instruction list, typed and range-checked fields | `test_form_is_signed_and_runs_local_compute_verified`, `test_form_instruction_is_a_fixed_choice_and_fields_are_typed` |
| Failures are visible | gateway down gives 502, a timeout 504, a non-JSON reply an error; every gateway error type has plain words | `test_gateway_outage_and_timeout_fail_visibly`, `test_every_gateway_error_type_has_plain_words` |
| Body limits | `WS_MAX_BODY_BYTES`, enforced while reading | `test_body_size_limit` |

### Helpdesk integration: pre-signed tickets

A helpdesk system whose ticket export holds a source key pinned in a contract (for example
`helpdesk-ticket-export@1` on `ticket_summary`) can send tickets through the workspace to the external AI,
protected. The workspace only passes the signature through. It never signs free text, and the browser never signs
anything.

```http
POST /api/integrations/v1/tickets
Authorization: Bearer <integration token>          # sha256 listed in WS_INTEGRATIONS_FILE
Content-Type: application/json
X-PGW-Input-Source: helpdesk-ticket-export@1
X-PGW-Input-Timestamp: <unix seconds>
X-PGW-Input-Nonce: <32-64 hex>
X-PGW-Input-Signature: <hex>

{"purpose": "ticket_summary", "messages": [...], "pgw_subjects": {...}}
```

- The source signs, with `pgw.product.attest.sign(…, app_id="hr-workspace")`, exactly the `messages` and
  `pgw_subjects` it sends. The workspace forwards both unchanged, with the four headers.
- If anything changes after signing, the gateway treats the input as unattested and keeps it inside.
- The response is JSON: `answer`, a plain-language `error`, and `view` (the adapted report). Its HTTP status mirrors
  the gateway's.
- The endpoint is machine-to-machine. It is not behind SSO, it is disabled (404) unless `WS_INTEGRATIONS_FILE` is
  set, and it accepts only the free-text purposes. Restrict network access to it to the helpdesk.

### The transparency report

The UI is driven only by real gateway responses: their `pgw_report` (docs/TRANSPARENCY_REPORT.md), HTTP status and
`X-PGW-Route`. `pgw/workspace/report.py` adapts the report into the view the page renders. It drops malformed entries
rather than guessing, and its alias tables make a small schema change a one-line fix. `tests/fixtures/pgw_report_*.json`
are recorded from a real production-mode gateway. Regenerate them with
`PYTHONPATH=. python -m tests.workspace_harness record`.

**Limits.**

- The workspace does not use streaming.
- Gateway-side PII protection is exactly as strong as the gateway. The checks shown are the gateway's, in its own
  words; "egress clean" cannot see a value that detection missed.
- The proxy trust model assumes that the SSO proxy overwrites the identity header and that only it can reach the
  workspace.
