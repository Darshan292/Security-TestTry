# Transparency report API (`pgw_report` v1)

This document describes what the gateway tells the calling app about one request: the stages that ran, what was
found, what was replaced or withheld, and exactly what left the gateway. It is meant for building a truthful
product UI. Code: `pgw/gateway/report.py`. Security reasoning: [SECURITY_BOUNDARY.md §9](../SECURITY_BOUNDARY.md#9-transparency-report).
Tests: `tests/test_transparency_report.py`.

## Who gets it

- The report is returned **only** to the authenticated app that sent the request, in that request's own response.
- It is returned only if that app's entry in the signed registry (`registry/apps.yaml`) says `transparency: true`.
  The default is `false`. Any value other than `true` or `false` stops startup. Changing the flag requires
  re-sealing the configuration (`python -m pgw.product.integrity seal`), like any other registry edit.
- An app without the flag gets byte-for-byte the same responses as before this feature existed.
- The report is never logged, never written to the egress or restore ledger, and never sent to a provider.
- No report is returned before the app is authenticated: a 401 or any earlier failure has none.

## Where it appears

| Response | Location |
|---|---|
| Non-stream 200 | top-level key `"pgw_report"`, the last key of the chat-completion object |
| Stream | one SSE event `data: {"pgw_report": {...}}` after the last content chunk, the finish chunk, the usage chunk and the `: pgw-gate-events` comment, immediately before `data: [DONE]` |
| Stream interrupted after it started | one `data: {"pgw_report": {...}}` event (outcome `error`) immediately before the terminal `data: {"error": {...}}` event (there is no `[DONE]`) |
| Error (4xx/5xx JSON) after authentication | `error.pgw_report`, inside the error object, beside `type` and `message` |
| 401 / before authentication | none |

## Schema

```text
{
  "version": 1,
  "request_id": "req_…",                       // equals the X-PGW-Request-Id header
  "purpose": "ticket_summary" | null,          // null if the purpose header was missing or not permitted
  "contract": "ticket_summary@8" | null,       // null before the contract was resolved, or on the dev legacy path
  "route": "EXT_PSEUDO" | "LOCAL_COMPUTE" | "RECORDS" | "PRIVATE_LOCAL" | null,
  "outcome": "sent" | "kept_inside" | "refused" | "error",
  "input_source": {"id": "helpdesk-ticket-export@1" | null, "verified": true | false},
  "stages": [ {"id": STAGE, "status": "done" | "skipped" | "failed", "ms": 0.412}, … ],
  "detected": [ ENTRY, … ],                    // at most 2000 entries
  "truncated": false,                          // true when `detected` was cut at 2000
  "counts": {"replaced": 4, "removed": 2, "by_type": {"PERSON": 2, "PAN": 1, …}},
  "outbound": OUTBOUND | null,
  "restore": {"restored": 3, "kept_withheld": 1} | null,
  "checks": [ {"id": CHECK, "status": "pass" | "fail" | "n/a", "label": "…"}, … ],   // always all four, this order
  "error": {"type": "input_not_source_sanitized", "message": "…"} | null
}
```

### `outcome`

| Value | Meaning |
|---|---|
| `sent` | 200 on the external route (EXT_PSEUDO or LOCAL_COMPUTE): protected text went to the external provider |
| `kept_inside` | 200 on PRIVATE_LOCAL: the request went only to the in-boundary model |
| `refused` | the gateway decided not to proceed: every 4xx, and the 500 `PGW_EGRESS_VIOLATION` (nothing was sent) |
| `error` | anything else that failed: 5xx (provider down, detector or ledger unavailable, internal error), or a stream that broke after it started |

### `stages`

The stages always appear in this order, but only the stages that were reached are listed. A refused or failed
request lists the stages up to and including the one that stopped it, which has status `failed`.

| id | What it covers |
|---|---|
| `received` | from the request's arrival to a validated body: reading, JSON, parameters, sizes, declared subjects |
| `verify_source` | checking the source attestation signature (`skipped` on the dev legacy path) |
| `content_gate` | the special-category content gate (pgw.ml.gate). **Listed only when the contract declares a `content_gate`**; a triggered gate with action PRIVATE continues on PRIVATE_LOCAL, with REFUSE stops with 422 `special_category_content`; a gate that cannot answer stops with 503 `content_gate_unavailable` (`failed`) |
| `structure_check` | the record-structure check of the free text (pgw.detect.structure; SECURITY_BOUNDARY.md §11). **Listed on EXT_PSEUDO contracts once the source is verified.** No structured region: `done`. A table, export or JSON/XML/key-value records: with `structured_content: {action: PRIVATE}` and an in-house model the request continues on PRIVATE_LOCAL (`done`); otherwise it stops with 422 `structured_data_in_free_text` (`failed`). A check that errors stops with 503 `structure_check_unavailable`, one that runs out of time with 503 `protection_timeout` (`failed`) |
| `compute` | the local computation of LOCAL_COMPUTE; `skipped` on other routes |
| `protect` | the script check (C4), detection and replacement |
| `egress_check` | building the exact outbound bytes and checking them for every value detected or declared in this request |
| `provider` | the external provider call, or the in-boundary model call on PRIVATE_LOCAL |
| `restore` | the output gate and putting real values back into the answer (masking C3 and long digit runs on PRIVATE_LOCAL) |

`ms` is real measured wall time, in milliseconds, rounded to 3 decimals. No synthetic delays are added. Skipped
stages have `0.0`. On a stream, `provider` is the stream's wall time minus the time spent restoring, and
`restore` is the time spent restoring.

A `verify_source: failed` stage does not always stop the request. On a contract whose unattested input goes to the
in-boundary model, the request continues on PRIVATE_LOCAL. In that case `protect`, `egress_check` and `compute` are
`skipped`.

### `detected` entries

On EXT_PSEUDO, one entry is returned per protected occurrence (span) in the caller's original strings:

```json
{"message": 0, "part": null, "start": 41, "end": 54, "type": "PERSON", "class": "C1",
 "action": "alias", "placeholder": "[PERSON_3JJT5F]", "source": "detected"}
```

- `start`/`end` are code-point offsets (Python/JS string indices for BMP text) into the caller's ORIGINAL
  `messages[message].content` string. `input[start:end]` is the detected value as the caller wrote it.
- `part` is set for list-form content: offsets are then into `messages[message].content[part].text`. Otherwise
  it is `null`.
- A span in a message's `name`, or in an assistant tool call's `arguments`, has an extra key
  `"field": "name"` or `"field": "tool_calls[k].function.arguments"`, and its offsets are into that string.
- `placeholder` is the exact text that went upstream in place of the span. For `remove` it is `"[PAN]"`. For
  `generalize` it is the generalised text. For `allow` it is `null`.
- Applying every non-null placeholder at its offsets to the original string gives exactly the corresponding
  outbound message text. There is one exception: an organisational contact that the contract declares (C0, held
  out of detection, not listed) leaves in the contract's spelling.

On LOCAL_COMPUTE, entries name fields of the JSON payload instead of offsets. They have no `start`/`end`:

```json
{"message": 0, "part": null, "field": "employees[0].annual_ctc", "type": "COMPENSATION", "class": "C2",
 "action": "withhold", "placeholder": null, "source": "declared"}
```

| `field` | Meaning |
|---|---|
| `employees[0].name`, … | each classified input field. C1 fields are aliased (with the placeholder used); C2 and C3 fields are withheld (`placeholder: null`) |
| `outputs.<name>` | each computed output (`source: "computed"`, `class` = the class it is derived from). TRANSFORMED outputs are aliased; INTERNAL outputs are withheld; RAW outputs are `allow` |
| `instruction` | a number in the free-text instruction that equalled a withheld value (`placeholder: "[COMPENSATION]"`), or a value detected in the instruction |
| `prompt` | a value detected in the gateway-built part of the prompt (rare) |

| Key | Values |
|---|---|
| `type` | `PERSON`, `EMAIL`, `PHONE`, `EMPLOYEE_ID`, `COMPENSATION`, `DATE_OF_BIRTH`, `AADHAAR`, `PAN`, `BANK_ACCOUNT`, `IFSC`, `CARD`, `UPI_ID`, `SECRET`, `IDENTIFIER`, `AADHAAR_LIKE`, `DERIVED` |
| `class` | `C0`, `C1`, `C2`, `C3`, `REF`, `D` |
| `action` | `alias` (scoped placeholder, restorable), `remove` (type marker, never restored), `generalize` (coarsened value), `withhold` (LOCAL_COMPUTE: never leaves in any form), `allow` (detected but crosses unchanged: a C0 type such as IFSC, or a RAW output) |
| `source` | `declared` (a declared subject or contract field covered the span), `detected` (a detector, the roster, this conversation's memory, or a union-mode plugin, docs/PLUGINS.md), `computed` (a LOCAL_COMPUTE output). Shadow-mode plugins never appear in the report: their disagreement counts are operator metrics |

PRIVATE_LOCAL has no `detected` entries, because nothing is replaced on the in-house route.

### `counts`

`counts` covers every entry, including entries cut by truncation. `replaced` counts `alias` and `generalize`
entries. `removed` counts `remove` and `withhold` entries. `by_type` counts entries per type, including `allow`
entries. Every count is of occurrences, not distinct values.

### `outbound`

```json
{"destination": "external", "provider": "mock", "model": "mock-1",
 "messages": [{"role": "system", "content": "Tokens in square brackets …"}, {"role": "user", "content": "…"}],
 "gateway_note_included": true, "sha256": "dba8…", "bytes": 534}
```

| Key | Meaning |
|---|---|
| `destination` | `external` (EXT_PSEUDO, LOCAL_COMPUTE) or `in_house` (PRIVATE_LOCAL) |
| `provider` | the upstream adapter: `openai` (any OpenAI-compatible endpoint, Groq included), `anthropic`, `mock`, or `in_house` |
| `model` | external: the model the request named. In-house: the model id that the in-boundary server reports in its reply, for example `extractive-standin` for the development stand-in, which is not a language model (the `X-PGW-Private-Model` header also says `standin`) |
| `messages` | external, 200 only: exactly the `messages` array of the upstream body (OpenAI shape). It includes the gateway's placeholder note as the first system message when `gateway_note_included` is true. For the Anthropic adapter, the wire body is the deterministic Anthropic mapping of these messages (system separated) |
| `sha256`, `bytes` | sha256 (hex) and length of the **exact** body bytes sent (the bytes the egress check ran on). The egress ledger records the same hash |

`outbound` is `null` when nothing was sent. `messages` is `null` in two cases:

- **in every error response**, including when the body had already been sent (provider failure, broken stream).
  The hash and size remain.
- **on PRIVATE_LOCAL.** What went in-house is the caller's unprotected input, C3 included. The gateway never
  returns C3 to the app, so it does not echo that text.

### `restore`

`restore` is `null` if the restore stage did not complete.

- `restored`: the number of placeholders put back with real values in the answer.
- `kept_withheld`: the number of placeholders and withheld-value markers left in place, because their type is never
  restored (e.g. `[PAN]`, shown to the user as "(withheld)"). On PRIVATE_LOCAL, it is the number of values the
  output gate masked.

Restore is reported only as counts, so no restored value (which may come from the conversation's memory) is in the
report.

### `checks`

| id | `pass` / `fail` | `n/a` when |
|---|---|---|
| `egress_clean` | the exact outbound bytes contain none of the values detected or declared **in this request** | nothing was checked (refused earlier, or PRIVATE_LOCAL: nothing left the boundary) |
| `declared_absent` | no declared subject (any spelling the matchers know) is in the outbound text | the request declared no subjects, or did not get that far |
| `source_verified` | the input carries a valid signature from a source that this contract allowlists | no contract (dev legacy path) |
| `c3_not_returned` | the answer returned to the app contains no C3 value (scan of the final answer text) | no answer was produced |
| `content_gate` | `pass`: no label of the contract's content gate reached its threshold; `fail`: one did, and the request was kept in-house or refused (this is the gate working, not an error) | never `n/a`: the check is **omitted** unless the gate ran (no gate in the contract, or the request stopped before it), so reports of other contracts are unchanged |
| `records_egress` | RECORDS route only: the protected prompt, with every computed token held out and aliases blanked, contains no input value the field schema did not classify KEEP (strings case-insensitively, numbers in any grouping or unit, dates in any common format), and the regex/identifier detectors find no identity or C3 value in the final text | never `n/a`: **omitted** unless the route is RECORDS and the check ran |

| `structure_check` | `pass`: no record-structured region in the free text; `fail`: one was found and the request was refused or kept in-house (the check working, not an error) | omitted unless the check ran (LOCAL_COMPUTE, unattested input diverted in-house, or stopped earlier) |

The 422 `structured_data_in_free_text` error also carries `regions`: up to ten `{format, records, fields}` entries
(format family and counts only; never a key, header or value).

`egress_clean: pass` is not a statement that the text is free of personal data. It cannot see a value that
detection missed.

### `error`

`error` is `null` on success. Otherwise it is the same `type` and `message` as the response's error object. These
messages are fixed strings, never built from content.

### `records` (RECORDS route only)

Present only for a contract with `route: RECORDS` ([HRMS_CONTRACTS.md](HRMS_CONTRACTS.md)) once the payload was
classified; absent otherwise, so reports of other routes are unchanged (the schema stays `version: 1`; the key is an
addition). `route` is `"RECORDS"`. It holds paths, dispositions and counts, never a value. `detected` lists the
aliased identities found in the prompt (`field: "prompt"`, `source: "declared"`), not every input value.

```text
"records": {
  "schema": "india_payroll_register@1", "starter_schema": true, "format": "csv",
  "rows": 2000,
  "fields": [ {"path": "Basic", "name": "basic", "disposition": "COMPUTE_ONLY", "selected": true, "values": 2000}, … ],
  "by_disposition": {"ALIAS": {"fields": 3, "values": 6000}, "NEVER": {"fields": 7, "values": 9100}, … },
  "unknown": {"fields": 2, "values_dropped": 4000, "paths": ["Blood Group", "Emergency Contact"],
              "paths_truncated": false},          // paths: the caller's own field names, at most 100; never upstream
  "invalid_values": 0, "keep_rejected": 0,
  "computation": {"name": "payroll_register_check", "rules": "india_payroll_statutory@1", "starter_rules": true},
  "outputs": [ {"name": "exceptions", "kind": "list", "egress": "RAW"},
               {"name": "total_gross", "kind": "amount", "egress": "INTERNAL"}, … ],
  "left": {"outputs": ["rows_checked", …], "aliased_identities": 74, "listed_rows": 37, "not_listed": 0},
  "disclosure": {"enforced": true, "config": "disclosure_control@1", "min_group_size": 5, "listing": "coarsen",
                 "differencing": true, "quasi_identifiers": ["department", "pin_district", …],
                 "cells_suppressed": 2, "cells_suppressed_complementary": 1, "totals_suppressed": 0,
                 "groups_merged": 0, "groups_suppressed": 0, "rows_coarsened": 37, "rows_attributes_withheld": 0,
                 "lists_withheld": 0, "queries_refused": 0, "queries_compared": 3}
}
```

`disclosure` is an addition: small-group control, described in [HRMS_CONTRACTS.md](HRMS_CONTRACTS.md) "Small
groups". It holds counts and schema field names only, never a value. When the differencing check refuses a query,
the response is 422 `disclosure_control_refused` and nothing is sent. The `pgw_report` inside the error carries the
same section with `queries_refused: 1`.

## Size

`detected` has at most 2000 entries. Past that, it is cut and `truncated` is `true`, while `counts` still covers
everything. The other parts are bounded by the request limits (`max_messages`, `max_content_chars`, the
contract's `max_chars`). Building the report reads the detections that protection already produced, and adds
no measurable latency (`test_building_the_report_adds_no_measurable_latency`).

## Examples

All values are synthetic.

**EXT_PSEUDO, 200.** The input is `[{"role": "user", "content": "Imran Ansari (KD-11914) says PAN ZXWPA3889P was
rejected."}]`, with the requester declared as Imran Ansari.

```json
"pgw_report": {
  "version": 1, "request_id": "req_79ead8d163e44160b12be8a8", "purpose": "ticket_summary",
  "contract": "ticket_summary@8", "route": "EXT_PSEUDO", "outcome": "sent",
  "input_source": {"id": "helpdesk-ticket-export@1", "verified": true},
  "stages": [
    {"id": "received", "status": "done", "ms": 1.887}, {"id": "verify_source", "status": "done", "ms": 1.63},
    {"id": "compute", "status": "skipped", "ms": 0.0}, {"id": "protect", "status": "done", "ms": 25.056},
    {"id": "egress_check", "status": "done", "ms": 0.293}, {"id": "provider", "status": "done", "ms": 0.084},
    {"id": "restore", "status": "done", "ms": 1.301}],
  "detected": [
    {"message": 0, "part": null, "start": 0, "end": 12, "type": "PERSON", "class": "C1", "action": "alias",
     "placeholder": "[PERSON_1AHNXX]", "source": "declared"},
    {"message": 0, "part": null, "start": 14, "end": 22, "type": "EMPLOYEE_ID", "class": "C1", "action": "alias",
     "placeholder": "[EMPLOYEE_ID_52RH8Z]", "source": "detected"},
    {"message": 0, "part": null, "start": 33, "end": 43, "type": "PAN", "class": "C3", "action": "remove",
     "placeholder": "[PAN]", "source": "detected"}],
  "truncated": false,
  "counts": {"replaced": 2, "removed": 1, "by_type": {"PERSON": 1, "EMPLOYEE_ID": 1, "PAN": 1}},
  "outbound": {"destination": "external", "provider": "mock", "model": "mock-1",
    "messages": [
      {"role": "system", "content": "Tokens in square brackets such as [PERSON_...] are placeholders … "},
      {"role": "user", "content": "[PERSON_1AHNXX] ([EMPLOYEE_ID_52RH8Z]) says PAN [PAN] was rejected."}],
    "gateway_note_included": true,
    "sha256": "dba872292e9ee7c049a1e21aac0fda28a69058851dc2c6d5deab638f5943dfea", "bytes": 534},
  "restore": {"restored": 2, "kept_withheld": 1},
  "checks": [
    {"id": "egress_clean", "status": "pass", "label": "No value detected or declared in THIS request appears in what left"},
    {"id": "declared_absent", "status": "pass", "label": "No declared subject appears in what left"},
    {"id": "source_verified", "status": "pass", "label": "The input carries a valid signature from a source this contract allowlists"},
    {"id": "c3_not_returned", "status": "pass", "label": "No government or financial identifier (C3) appears in the answer"}],
  "error": null
}
```

**LOCAL_COMPUTE, 200 (salary comparison).** Some entries are omitted here.

```json
"detected": [
  {"message": 0, "part": null, "field": "employees[0].name", "type": "PERSON", "class": "C1", "action": "alias",
   "placeholder": "[PERSON_83CV2Z]", "source": "declared"},
  {"message": 0, "part": null, "field": "employees[0].annual_ctc", "type": "COMPENSATION", "class": "C2",
   "action": "withhold", "placeholder": null, "source": "declared"},
  {"message": 0, "part": null, "field": "outputs.difference", "type": "DERIVED", "class": "C2", "action": "alias",
   "placeholder": "[DERIVED_1EFWN1]", "source": "computed"},
  {"message": 0, "part": null, "field": "outputs.ranking", "type": "DERIVED", "class": "C2", "action": "allow",
   "placeholder": null, "source": "computed"},
  {"message": 0, "part": null, "field": "outputs.higher_ctc", "type": "DERIVED", "class": "C2",
   "action": "withhold", "placeholder": null, "source": "computed"}]
```

**Refused (422, unsigned input on a contract that refuses it).**

```json
{"error": {"type": "input_not_source_sanitized", "message": "this purpose only sends input signed by …",
  "attestation": "missing",
  "pgw_report": {"version": 1, "request_id": "req_bf7677cfa86146759fe7f42e", "purpose": "salary_comparison",
    "contract": "salary_comparison@5", "route": "LOCAL_COMPUTE", "outcome": "refused",
    "input_source": {"id": null, "verified": false},
    "stages": [{"id": "received", "status": "done", "ms": 0.227},
               {"id": "verify_source", "status": "failed", "ms": 0.027}],
    "detected": [], "truncated": false, "counts": {"replaced": 0, "removed": 0, "by_type": {}},
    "outbound": null, "restore": null,
    "checks": [{"id": "egress_clean", "status": "n/a", "label": "…"},
               {"id": "declared_absent", "status": "n/a", "label": "…"},
               {"id": "source_verified", "status": "fail", "label": "…"},
               {"id": "c3_not_returned", "status": "n/a", "label": "…"}],
    "error": {"type": "input_not_source_sanitized", "message": "this purpose only sends input signed by …"}}}}
```

**PRIVATE_LOCAL, 200 (unsigned input on a contract that keeps it in-house; development, stand-in model).**

```json
"route": "PRIVATE_LOCAL", "outcome": "kept_inside", "input_source": {"id": null, "verified": false},
"stages": [{"id": "received", "status": "done", "ms": 0.241}, {"id": "verify_source", "status": "failed", "ms": 0.031},
           {"id": "compute", "status": "skipped", "ms": 0.0}, {"id": "protect", "status": "skipped", "ms": 0.0},
           {"id": "egress_check", "status": "skipped", "ms": 0.0}, {"id": "provider", "status": "done", "ms": 1.248},
           {"id": "restore", "status": "done", "ms": 0.616}],
"detected": [],
"outbound": {"destination": "in_house", "provider": "in_house", "model": "extractive-standin", "messages": null,
             "gateway_note_included": false, "sha256": "571e…", "bytes": 116},
"restore": {"restored": 0, "kept_withheld": 2}
```

**Stream (tail).**

```text
data: {"id": "mock-1", "object": "chat.completion.chunk", …, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

: pgw-gate-events=0

data: {"pgw_report": {"version": 1, …}}

data: [DONE]
```

## Deviations from the draft spec (PGW_REPORT_SPEC.md)

1. **`outbound.messages` is `null` on PRIVATE_LOCAL and in every error response.** The draft asked for the text
   as sent. On PRIVATE_LOCAL that text is the caller's unprotected input, C3 included, and echoing it would break
   the rule that the gateway never returns C3 to the app. Errors never carry text. `sha256` and `bytes` are still
   given, and remain truthful.
2. **`action: "allow"`** (not in the draft's enum) marks a span that was detected but crossed unchanged: a C0 type
   such as IFSC, or a RAW LOCAL_COMPUTE output. Hiding these entries would make the report untruthful.
3. **Top-level `truncated`** is always present (`false` or `true`), not only when truncation happened.
4. **`field`** also appears on EXT_PSEUDO entries whose span is in a message `name` or in tool-call `arguments`.
   On LOCAL_COMPUTE, `field` takes the extra values `outputs.<name>`, `instruction` and `prompt`.
5. **`counts`** counts occurrences, including `allow` entries in `by_type`. The draft's example numbers were not
   consistent with each other.
6. **`purpose`, `contract` and `route` can be `null`**, for refusals that happen before the purpose or contract is
   known (e.g. a missing or forbidden purpose header).
7. **Stream interruption** is reported as an extra `pgw_report` event before the terminal error event. The draft
   did not cover this case.
8. **Organisational contacts** that the contract declares (C0, held out of detection) are not listed in `detected`,
   and they leave in the contract's spelling.
