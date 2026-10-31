# HRMS field contracts (route `RECORDS`)

HRMS data does not arrive as prose. Workday, UKG/Kronos, SAP Fieldglass, SuccessFactors, payroll registers and
Active Directory all export **typed fields with known names**. Pasting such an export into the free-text path makes
the gateway flatten it and guess, value by value, with detectors; an independent test of exactly that found 14% of
personal values leaking, special-category codes (sick, maternity, oncology, dialysis, criminal record check)
crossing verbatim, gender/marital status/nationality always crossing and TDS crossing raw.

The `RECORDS` route classifies values by **field**, not by guessing. It is the LOCAL_COMPUTE pattern
(`pgw/product/local_compute.py`) extended from a few typed form fields to whole exports:

```
signed export ──▶ verify source signature ──▶ parse (bounded; JSON / CSV / XML) ──▶ classify every field by the
sealed field schema (unknown → NEVER) ──▶ project the fields the purpose SELECTS ──▶ deterministic local computation
(sealed rules) ──▶ prompt = sealed template + computed tokens + aliased identities ──▶ protect (pipeline aliases
identities) ──▶ withheld-value check ──▶ detectors over the final text ──▶ provider ──▶ aliases restored
```

Code: `pgw/product/fieldschema.py` (schemas, types), `pgw/product/records.py` (contract block, extraction,
rendering, egress check), `pgw/product/hrcompute.py` (computations), `pgw/product/recparse.py` (safe parsers).
Small-group disclosure control: `pgw/product/disclosure.py`.
Config: `registry/schemas/*.yaml`, `registry/rules/*.yaml` (including `disclosure_control.yaml`),
`contracts/{payroll_register_check, timecard_exceptions,contractor_budget,roster_question}.yaml`. Tests:
`tests/test_hrms_records.py` and `tests/test_disclosure_control.py`, with the seeded synthetic generators in
`tests/hrms_synth.py`.

## Dispositions

| Disposition | What happens to the value | Typical fields |
|---|---|---|
| `ALIAS` (entity) | scoped alias in the prompt; restored in the answer | names, employee ids, usernames, emails, manager references, work orders (`IDENTIFIER`) |
| `REMOVE` | dropped | addresses, phones, PAN/UAN/ESIC/Aadhaar/bank a/c/IFSC, free text, titles, group memberships |
| `GENERALIZE` (method) | only the coarse form exists for the purpose: `year`, `month` (dates), `band` (amounts, sealed edges, every band ≥ 20% wide), `pin_prefix3` (3-digit PIN = sorting district) or `drop`, `bitflag` (one bit → one of two labels) | date of birth, hire date, PIN, `userAccountControl` |
| `COMPUTE_ONLY` | exact value used by the computation only; never leaves | every pay component, PF/ESIC/PT/TDS, net, bill rates, budgets, punches, hours |
| `NEVER` (category) | dropped at parse time, unavailable even to computations, never leaves in any form, not even generalised; its words are also withheld | gender, marital status, nationality, religion, caste, disability, health, absence reason, leave type, BGV status/result, union, biometrics/photo |
| `KEEP` | may leave as is | only an enumeration from a sealed reference table, or a boolean, with a written justification |

`KEEP` is deliberately narrow: free text can never be `KEEP` (refused at startup), an enumeration may not contain
values with four or more digits, and a value outside the enumeration is not kept (counted as `keep_rejected` and
withheld). TDS, PF, ESI, PT and any pay-derived amount are compensation-derived (C2): `COMPUTE_ONLY`, or a band.

A `NEVER` field may declare a **presence flag**: a `COMPUTE_ONLY` boolean "this row carries a non-work value"
(`presence_flag: {name: is_absence, work_values: [REG, ...]}`). The timecard computation uses it to count absence
days without ever seeing the reason.

## Unknown fields → NEVER

Any path the schema does not name is treated as `NEVER`: dropped, its values withheld, the count reported and the
path names returned to the caller in the transparency report (never upstream, never logged). The alternative,
letting unknown fields through or classifying them by detectors, is exactly the failure this route replaces:
vendors add fields with every release, tenants add custom fields (`extensionAttribute9`, `customString7`), and a
schema can only vouch for what it names. Fail closed; add the field to the schema when you need it.

## Writing a schema

```yaml
# STARTER SCHEMA - verify against your tenant's actual field names before use.
kind: pgw-field-schema
schema: my_export            # = file name (registry/schemas/my_export.yaml)
version: 1
title: My export
starter: true                # shown in the report as starter_schema
formats: [csv, json]         # json | csv | xml
records_at: "$[]"            # the records array: "$[]" top level, "data[]", "d.results[]",
                             # "Get_Workers_Response.Response_Data.Worker[]"
fields:
  - {path: Employee_Code, name: employee_id, type: string, disposition: ALIAS, entity: EMPLOYEE_ID,
     csv: ["Employee Code", "Emp ID"]}            # CSV headers (case/spacing ignored); JSON keys may use them too
  - {path: "Worker_Data.Personal_Data.Contact_Data.Email_Address_Data[].Email_Address", name: email,
     type: string, disposition: ALIAS, entity: EMAIL}   # nested objects and arrays
  - {path: Worker_Data.Personal_Data.Gender_Reference, name: gender, disposition: NEVER, category: gender,
     subtree: true}                               # NEVER/REMOVE may cover a whole subtree
  - {path: Basic, name: basic, type: amount, disposition: COMPUTE_ONLY}
  - {path: DOB, name: birth_year, type: date, disposition: GENERALIZE, method: year}
  - {path: Department, name: department, type: enum, disposition: KEEP, enum: [Finance, Legal],
     justification: "Department names from the reference table; shared by many employees"}
```

- Paths are relative to each record; `[]` marks an array (a single object is accepted where an array is declared,
  as XML produces). XML: namespace prefixes are dropped, attributes are `@name`, text next to attributes `#text`;
  a SOAP `Envelope/Body` is unwrapped.
- Types: `string`, `amount` (₹/Rs/INR, Indian or western grouping, `(1,234)` negative), `number`, `int`, `date`
  (ISO, DD/MM/YYYY or `date_order: MDY`, DD-Mon-YYYY, Mon DD YYYY, AD generalized time, FILETIME), `datetime`,
  `time`, `bool`, `enum`, `any` (NEVER/REMOVE only).
- `optional: true`: a schema field allowed to be absent from the whole input. A selected, non-optional field absent
  from every record is a 400 naming the field; absent or unparseable in one row it is `MISSING:<field>` /
  `INVALID:<field>` for that row. Nothing passes silently.
- Startup refuses: a disposition outside the list, ALIAS with a non-identity entity, KEEP on free text or without a
  justification, a band narrower than 20%, duplicate names/headers/paths, a field inside another field's subtree,
  and **any field whose path, name or CSV header carries a special-category word** (gender, marital, religion,
  caste, nationality, disability, maternity, sick, BGV, criminal, photo, "leave type", "absence reason", "trade
  union", ...) unless it is NEVER (`fieldschema.SPECIAL_WORDS`, `SPECIAL_BIGRAMS`).

### Starter-schema caveats

The shipped schemas (`workday_worker_rest`, `workday_get_workers`, `ukg_timecard`, `sap_fieldglass_worker`,
`successfactors_employee`, `india_payroll_register`, `ad_user`) are **starters**: names follow the vendors' public
naming as best known (Workday REST/WSDL, SuccessFactors OData PerPerson/EmpJob, LDAP attributes, common register
headings) but are not claimed exact, and they vary by version, report definition, expansion and tenant
customisation. Before use: export a sample from your tenant, compare every path/header, check the
transparency report's `unknown.paths` on a real (test-tenant) export, replace the enumerations (departments, cost
centres) with your reference tables, and re-seal. Unknown paths are safe (dropped) but make computations fail with
`MISSING`; misnamed paths are what you are looking for.

## Purpose contracts

```yaml
route: RECORDS
records:
  sources: [india_payroll_register]   # schema ids (each must define every selected name the same way)
  select: [employee_id, employee_name, basic, ...]   # projection first: nothing else is read for the purpose
  label: [employee_name, employee_id] # ALIAS fields that name a row in listed outputs
  show: [department]                  # KEEP/GENERALIZE fields shown next to listed rows (roster_question)
  compute: payroll_register_check
  rules: india_payroll_statutory      # registry/rules/<id>.yaml
  max_rows: 50000                     # ceiling 200,000
  max_bytes: 20000000                 # request body and payload bound (ceiling 64 MiB); replaces the profile's
                                      # free-text limits for this purpose only
  max_listed: 200                     # rows listed in the prompt (the rest counted)
  max_prompt_chars: 30000             # output bound
  timeout_s: 30                       # compute deadline (ceiling 120)
  min_group_size: 10                  # optional: raises the sealed disclosure-control k for this purpose (never
                                      # lowers it; see "Small groups")
outputs:                              # per computed output; anything unlisted is INTERNAL
  exceptions: {derived_from: C2, egress: RAW, justification: "..."}
  total_gross: {derived_from: C2, egress: INTERNAL}
  overall_utilisation_pct: {derived_from: C2, egress: TRANSFORMED, band: [0, 50, 80, 100], unit: "%"}
prompt_template: |                    # only non-INTERNAL outputs; no free-text instruction field
```

Output kinds (`hrcompute.KINDS`): `count`, `codes` (fixed vocabulary with counts), `label`, `list` (rows named by
aliased identities, with codes/bands), `groups` (sealed KEEP value → token) may leave `RAW` with a justification;
`amount` and `percent` are `INTERNAL` or `TRANSFORMED` as a sealed band, **never RAW** (startup refuses).
There is no caller-written instruction on this route: the template is the whole prompt. Contracts must also state
`data_classes` (C1 `ALIAS` or `REDACT`), `purpose_dependent` stances and `external_input` (unsigned payloads are
refused with 422 exactly like free text); `content_gate` does not apply.

### Computations (`pgw/product/hrcompute.py`)

| Computation | Checks | What leaves |
|---|---|---|
| `payroll_register_check` | Gross = Σ earnings and Net = Gross − Σ deductions (tolerance); PF = rate × PF wages, capped at the wage ceiling or on full wages (both accepted), zero allowed above the ceiling; ESIC only while gross ≤ ceiling (rounded up; above the ceiling flagged for review); PT by state slab (February top-up; several schedules accepted where the law differs by gender; half-yearly states not checked); TDS sanity (TDS > 0 while annualised taxable ≤ the nil-tax threshold; TDS share of gross above a bound) | counts, codes, per-row aliased employee + codes |
| `timecard_exceptions` | missing punch, punch order, shift > max hours, spread-over > max, weekly hours > max (ISO week), overtime without approval; absence days counted from the presence flag | counts, codes, per-employee aliased identity + code × n, absence counts (never reasons) |
| `contractor_budget` | spend vs PO/budget (over / near), bill rate vs approved rate, missing/negative/no budget | counts, codes, utilisation and rate-excess **bands**, cost-centre bands |
| `roster_question` | `params: {filter: [{field, op, value}], group_by, list}` on selected KEEP/GENERALIZE fields; values must be enumeration members, booleans, sealed labels or the generalised format (`2024`, `2024-03`, `560xxx`) | counts, filter text, aliased matches, group counts |

Statutory and policy values live in `registry/rules/*.yaml` (sealed, signed), each with its source cited and a
"verify current rates" note; nothing is a constant in code. **Add a computation:** write a function
`fn(rows, params, rules, ctx) -> {name: Out(kind, value)}`, a rules parser that raises `SchemaError` on anything
malformed, an `inputs(rules)` map of the selected names it reads and the dispositions each may have, and register
it in `hrcompute.COMPUTATIONS` with its output catalog. Rows give typed values (`None` + `row.errors[name]` when
missing/invalid; emit `MISSING:`/`INVALID:` codes, never skip silently). Add hand-computed fixtures with the edge
cases (ceilings, slab boundaries, zero, negative, missing) to `tests/test_hrms_records.py`.

## Small groups (disclosure control)

Computed results, aliases and KEEP/GENERALIZE values can still single a person out. A count of 1 in a cross-tab
tells the provider that person's attribute. An alias listed with a rare combination of attributes can be linked to a
public profile: "[PERSON_7], the only Staff Engineer in cost centre 4410 hired in 2019, band B4". Every RECORDS
response therefore passes `pgw/product/disclosure.py` before it is rendered. The configuration is the sealed
`registry/rules/disclosure_control.yaml`. A missing, modified or unsealed file stops startup.

```yaml
kind: pgw-disclosure-control
config: disclosure_control
version: 3
min_group_size: 5          # k. Below 3 only with min_group_size_override: {justification: "..."} (20+ chars);
                           # never below 2. A contract's records.min_group_size may raise it, never lower it.
listing: coarsen           # coarsen | withhold | alias_only
differencing:              # durable audit history per (tenant, dataset); see "Repeated queries" below
  enabled: true
  scope: {separate_apps: false, separate_purposes: false}   # true needs a justification: weaker
  per_caller: {new_queries_per_minute: 20, new_queries_per_day: 500}   # caller = authenticated app
  max_stored_keys_per_scope: 2000000 # record hashes kept per scope (16 bytes each)
  max_cells_per_scope: 100000        # distinct revealed record sets per scope
  max_atoms_per_scope: 20000         # bounds the audit's cost
  query_log_per_scope: 10000         # metadata rows (predicate HMACs, day numbers); older rows compacted
  max_scopes: 10000
  audit_budget: 400000               # search steps per audit trial; exhausted -> refused (fail closed)
  audit_max_ms: 1500                 # wall-clock bound of one response's audit (50..60000); exceeded -> refused
rounding: {enabled: false, base: 5}  # deterministic rounding of roster counts ("about 15"); off, see below
schemas:
  ad_user:
    record_key:            # the dataset's record identity for the history (every contract over the schema)
      fields: [username]
      normalise: {username: {casefold: true, strip_domain: true, strip_upn_suffix: true, strip_leading_zeros: false}}
    quasi_identifiers: []  # ADDED to the default: every KEEP and GENERALIZE field (every field whose value can leave)
    hierarchies:           # roll-ups of a KEEP enumeration, finest first (department -> function, title -> family)
      department: [{Finance: Corporate functions, Legal: Corporate functions, Sales: Customer-facing, ...}]
```

The gateway enforces these rules on what leaves:

| Output | Rule |
|---|---|
| Count tables (`roster_question`: total = matched + missing + did-not-match; matched = sum of the `group_by` counts) | Every cell defined by a quasi-identifier predicate with 0 < n < k is suppressed. The matched count prints `fewer than k`; any other cell prints `suppressed`. Then complementary suppression: the cheapest other cell of the same sum is suppressed (the record total first, then the smallest group). This repeats until an exact audit finds that no suppressed cell, and no small did-not-match cell, is determined by the printed numbers. Zeros are printed. The audit propagates intervals over the tree of sums, which is exact for these tables: every integer inside a bound is feasible. |
| A roster filter matching 1..k-1 records | Prints `Matching: fewer than k` and `none listed`; every group count is suppressed. If the matched count is suppressed for any other reason, the listing is withdrawn too, because the listing would print the count. |
| Grouped bands (`contractor_budget` per cost centre) | A cost centre with fewer than k people (distinct workers, not work orders) is merged into `other cost centres`. That merged line is printed only if it has at least k people; otherwise it shows `suppressed`. |
| Listings with attributes (`show` fields, in any computation) | Each listed alias's printed attribute combination must be shared by at least k people of the population the listing is drawn from: the matched records for a roster query, the whole input for an exception list. First comes full-domain coarsening, one level per field for the whole listing, starting with the field that has the most distinct values: month -> year -> 5-year range, PIN `560xxx` -> `56xxxx`, sealed enumeration roll-ups, and finally dropping the column. Coarsening continues while more than k rows fail. Rows that still fail print `attributes withheld (fewer than k share them)`. The withheld rows must themselves be indistinguishable among at least k people; if they are not, more classes are withheld. A `group_by` value whose count was suppressed is withheld in the listing of the same response, and that field is not coarsened, so the listing cannot re-print a suppressed count. With `withhold`, coarsening is skipped. With `alias_only`, attributes are never printed next to aliases. |
| Roster listings in the differencing history | Everything a printed listing lets the reader count is a history cell, audited (R1/R2) with every other count: the listed rows (lines can be counted), the rows not listed (`... and N more not listed`), and each attribute class -- the rows printed with one attribute text, `attributes withheld` included -- so per-attribute row counts are sums of audited cells. How many rows are listed is decided before the audit (`max_listed` and the character budget, every line costed as if it carried attributes); a cut that would leave 1..k-1 rows not listed, or list only 1..k-1, lists fewer. If the attribute classes would break the audit, the history withholds the attributes of the whole listing (`attributes withheld (disclosure control)` on every row, one class) before anything else is refused. Tester t6 (`in [Finance, Legal, Sales]` listed as Finance 12 / withheld 10, then `Sales` 8 -> Legal 2) is refused at the second question; in the reverse order the listing prints its attributes withheld. Tests: `tests/test_disclosure_listing.py`, `test_random_roster_sequences_with_listings_never_determine_a_small_group`. Exception lists of payroll, timecard and contractor contracts are not in the history (below). |
| Exception lists (payroll, timecard, contractor) | A row describes the listed person by design. It carries the alias and codes or bands only, with no quasi-identifier, unless the contract selects `show` fields; then the listing rule above applies. Code counts over the whole export are not conditioned on any quasi-identifier and are already contained in the per-person list, so they are printed. |
| Repeated queries (differencing) | A durable audit history per **tenant and dataset** -- shared by every app and every RECORDS purpose of the tenant, not per conversation (see "Repeated queries" below). It keeps every cell any answer revealed, printed numbers and suppression markers alike, as the set of keyed record hashes behind it, and audits the whole scope as linear constraints over record sets before anything is sent: no group of 1..k-1 records may have its size determined by any combination of answers, and no suppressed cell may be pinned by the markers' bounds. A response that would break this has its secondary cells (record total, missing count, group counts) suppressed as well; if its answer (the matched count) would break it, it is refused (422 `disclosure_control_refused`, `reason: differencing`, nothing sent). Per-caller rate limits apply to new questions only (429 `rate_limited`); a full scope refuses only new information (`history_full`); a store failure is 503 `disclosure_store_unavailable`. |

### Repeated queries: the differencing history

**Threat model.** The adversary is the external LLM provider. It sees every outbound request of the tenant: from
every conversation, user, app, purpose and day, and it keeps them. `X-PGW-Conversation-Id` is chosen by the caller
and is therefore not a security boundary. Answers that together pin a small group give it away, whichever
conversations, processes, contracts or exports they came from: "(Finance, Legal, Sales) by department: 22, Finance
12, Legal and Sales suppressed" and later "Sales: 8" give Legal = 2; "(Legal, Sales) 5", "(Sales, Support) 6",
"(Legal, Support) 5" give Legal = 2 although no two of them differ by fewer than k; "Finance: 6" on Monday's export
and "Finance: 7" on Tuesday's give the new joiner's department.

**Scope.** One history per (tenant, dataset). The tenant comes from the authenticated app entry, never from a header.
The dataset identity is the payload's source schema id plus the contract's optional sealed label `records.dataset`
(`[a-z][a-z0-9_.-]`, up to 64 characters; e.g. one label per legal entity whose exports must not share a history).
It is deliberately **not** the export's content hash (a refreshed export shares Monday's history), and by default it
does **not** include the app or the purpose: a second contract over the same data (`roster_pin` next to
`roster_question`) shares the history. `differencing.scope.separate_apps` / `separate_purposes` (sealed, with a
written justification) separate them; that is weaker, because the provider sees all of them.

**Record identity.** A record key is `HMAC-SHA256(scope key, normalised identity)` truncated to 16 bytes. The
identity is the schema's sealed `record_key` (default: its first employee-identifier field, `username` for
`ad_user`), the same for every contract over the schema; a roster contract that does not select it does not bind.
Before hashing each value is normalised (`disclosure.normalise_identity`): Unicode NFKC; every whitespace, control
and format character (zero-width space, BOM) removed; then, per the sealed options of the field (all on by default
except the last): casefold, a Windows domain prefix stripped (`CORP\asha` -> `asha`), a UPN suffix stripped
(`asha@corp.example` -> `asha`), leading zeros of an all-digit id stripped (`strip_leading_zeros`, off by default:
`00123` and `123` are different people unless the organisation says otherwise). Upper-casing the usernames or adding
a domain prefix in the next export therefore no longer makes the same people look new. A repeated identity in one
set, or a record with no identity, counts with multiplicity. The scope key is `HMAC(K, scope id)`, where K is
derived from the gateway key store for the tenant (`keys.derive(tenant, "disclosure-history", key id)`); the key id
is pinned per scope with a key-check value (rotation keeps the history comparable; a retired pinned key id or a
different master key fails closed, 503).

**The audit** (`pgw/product/setaudit.py`). Every cell of every answer is stored with what the reader learned about
its size: an exact number, or an interval -- `suppressed` is [1, inf) because zeros are printed, `fewer than k` is
[1, k-1], `withheld` is [k, inf), a rounded count is the range that rounds to it. The keys seen so far are
partitioned into **atoms** (records with the same membership in every cell); every cell is a union of atoms. The
exact counts span a linear space L over the atoms, kept in reduced row echelon form modulo 2^61-1 (sparse rows,
pivots preferably on atoms of at least k records). The reader is assumed to know every set relation between its
answers (which atoms exist) and to use linear algebra, integrality and the intervals. After every answered query:

- **R1 (exhaustive).** No union W of atoms with 1 <= |W| <= k-1 has its size determined by the exact counts
  (1_W in L), except an export's own record total. Such a W lies in L n span(small atoms); once no row with a small
  pivot has an entry on a large atom, those rows are a basis of it and W is a 0/1 sum of at most k-1 of them; the
  search is exhaustive per connected component of rows (a complete branching search: a partial sum that is not
  yet a 0/1 vector of weight 1..k-1 can only be completed by a row that changes one of its offending columns).
- **R2 (certified).** Every suppressed cell of 1..k-1 records and every atom of 1..k-1 records has a verified
  alternative: an integer atom-count vector, different on it, that satisfies every exact count exactly, every
  interval and non-negativity (a kernel vector of L, or the sum or difference of two, checked in integer
  arithmetic). Five suppressed singletons whose union is later printed as 5 have none: refused.

Both fail closed: an audit that does not finish within `audit_budget` steps per trial or `audit_max_ms` of wall
clock for the whole response (all its suppression trials; the clock is read cooperatively inside the search) is
refused with 422 `reason: audit_budget` and a message that says the audit could not be certified and that a
narrower or broader question may be answered -- distinct from 422 `reason: differencing` ("would determine the size
of a group of fewer than k records"). Neither message carries a value. Metric: `refused_audit_budget`. **History-aware complementary suppression:** when a
response would break R1 or R2, the gateway first audits the response with every secondary cell (record total,
missing count, group counts, a listing's attribute classes as one unit) suppressed -- if even that fails, the query
is refused at once -- and then, on top of that accepted state, suppresses the one secondary cell whose suppression
alone suffices, preferring cells that touch the problem (never one the reader already knows exactly), and audits
again; if that search uses two thirds of `audit_max_ms`, the response is answered with every secondary cell
suppressed (already audited). (Printing instead as many cells as the audit allows answered fewer later questions in
the benchmark below: more exact counts in the history.) The auditor applies these steps as
nested tentative levels and searches R1 only for sums that contain a row the step changed. "Sales: 8" first, then the grouped question, prints Finance as `suppressed`; after
"(Finance, Sales): 20", "Finance" prints `Records: suppressed`. If even that is not enough, or the answer itself (the
matched count) would pin a small group, the query is refused. **Tests:** `tests/test_disclosure_audit.py` checks
every accepted step of random set families and of random roster query sequences against two independent brute-force
adversaries (exact rational span over every set of 1..k-1 records; exhaustive enumeration of every integer
assignment consistent with every count and bound); `tests/test_disclosure_history.py` reproduces each bypass.

**Cost.** Refinement is O(|S|) per new cell; one exact cell is one sparse row reduction. Measured on a synthetic
5,000-person export with 300 random filtered / grouped questions (about 2,000 atoms, 500 cells): 0.1 s per audit on
average, 0.35 s at most; rebuilding a scope's audit state from the database (another process wrote, or a restart)
0.5 s. Bounded by the sealed caps on stored keys, cells and atoms per scope and the per-audit `audit_budget`.

**What is stored** (`HistoryStore`; SQLite, WAL, `synchronous=FULL`, one IMMEDIATE transaction per
check-and-record, so several gateway processes on a host cannot both answer a differencing pair; file mode 0600,
its WAL / shm sidecars too, directory tightened to 0700): per scope the labels (tenant, dataset; `*` for app and
purpose when shared), the pinned key id and key-check value, counters, the creation **day**; per distinct revealed
record set its size, bounds and members as 16-byte keyed hashes; a bounded query log (`query_log_per_scope` rows:
sequence, **day number**, a 16-byte keyed HMAC of the predicate, a keyed HMAC of the caller, whether it added
information); per caller and day the number of new questions. **No filter value, field name, operator or timestamp
is stored in clear** (`test_history_file_modes_and_no_clear_filter_values_or_timestamps` scans the file and WAL).
Every check happens before anything is sent; a query is recorded only when it passes, in the same transaction. A
query that passed but later failed (egress check, provider error) stays recorded (conservative).

**Capacity, rate limits and compaction -- no lockout, no reset.** Last round a scope refused everything after 1,000
answered questions (about 17 s of scripting), locking the whole tenant out until a reset, and the reset reopened
every gap. Now:

- A question that adds no information (each of its cells is already stored with bounds at least as tight) is always
  answered and never counted (repeats, the same question on an unchanged export, questions whose sets are known).
- Per-caller limits on **new** questions: `new_queries_per_minute` (in memory, per gateway process) and
  `new_queries_per_day` (in the history, shared by every process); exceeding them refuses only that caller's new
  questions (429 `rate_limited`). The caller is the authenticated app: the gateway has no end-user identity (the
  workspace authenticates users by SSO but calls the gateway with its one app key), so all users of one app share
  its budget. To exhaust a scope's `max_cells_per_scope` (100,000) at 500 new questions a day, one caller needs weeks.
- A full scope (stored keys, cells or atoms) refuses only new information (422 `history_full`, logged as
  `disclosure_history_full`, counted as `refused_capacity`).
- Operator **compaction** (deliberate, logged, written reason) drops a scope's query log and its callers' daily
  counters and KEEPS every record set, so no earlier answer stops being compared:

      python -m pgw.product.disclosure history list    --db $PGW_STATE_DIR/disclosure_history.sqlite
      python -m pgw.product.disclosure history compact --db ... --tenant T --dataset D \
          --reason "CHG-1234: caller budget review approved by the DPO"
      python -m pgw.product.disclosure history log     --db ...

  There is no reset. Trade-offs: the record sets are never forgotten, so storage grows with distinct information
  (bounded by the caps; raise them in a sealed change for a busy scope); an epoch rotation was considered and not
  built, because forgetting exact answers is only safe together with coarsening that makes the old answers useless,
  which rounding is not (it does not stop differencing at rounding boundaries). Operator metrics:
  `app.state.pgw_disclosure_metrics.snapshot()` and `/healthz` (`disclosure_history`: answered, answered_repeat,
  suppressed_more, refused_differencing, refused_capacity, refused_rate, store_unavailable, scopes, queries, cells;
  counts only).

**Deletion and replacement stop startup.** The database carries a random id and a commit sequence; after every
commit that added information they are written to the high-water mark `<PGW_STATE_DIR>/disclosure_history.hwm.json`
(atomic, fsync'd, under a lock; the pattern of the config high-water mark `config_serial.json`). Production refuses to
start (`HistoryIntegrityError`, logged as `disclosure_history_integrity_failed`) when the database is missing or
empty, has another id (replaced), is older than the mark (restored from an older copy), or the mark is missing or
corrupt. A missing history is created silently only on the first production start of a fresh state directory (no
config high-water mark yet). After investigating, an operator recovers deliberately (written reason, admin log):

    python -m pgw.product.disclosure history recover --db ... --state-dir $PGW_STATE_DIR --reason "..."        # re-bind after a restore
    python -m pgw.product.disclosure history recover --db ... --state-dir $PGW_STATE_DIR --reason "..." --new  # empty history

`--new` is the only way to forget answers, and it is a gap the operator accepts in writing. (A state directory that
has started production before without any RECORDS contract has no history yet: the first start with one is refused
the same way; run `recover --new` once to create it.) The mark is not a MAC:
whoever can write both files consistently can also edit the history itself; it stops deletion, replacement and
rollback to an older copy, accidental or naive. Development only warns. Not for network filesystems.

**Consequence for refreshed exports.** A count printed for a group can be printed on a later export only if the
change does not pin a small group: with 1..k-1 joiners or leavers in that group the question is refused; with
1..k-1 joiners elsewhere the record total prints `suppressed` (the joiner count is a small group); an unfiltered
question on such an export is refused. This is the price of printing exact counts; it is what the provider would
otherwise learn.

**Rounding (evaluated, shipped off).** `rounding: {enabled: true, base: B}` prints every roster count (total,
matched, missing, groups) as `about B*round(n/B)` (half up; never below B for a non-zero count; 0 stays 0;
suppression markers are unchanged), deterministically: the same true count prints the same text in every cell and
response (`test_rounding_is_deterministic_and_consistent`). Default base 5. It is **off by default** because:
(1) it does not stop differencing at rounding boundaries (12 -> "about 10", 13 -> "about 15": a one-person change
across a boundary still shows), so the audit stays the control (it treats rounded counts as intervals); (2) within a
scope the audit already refuses every determined small group, so rounding adds little there; (3) a roster listing prints every matching
alias, so the exact count is recoverable by counting lines; (4) these counts are read and acted on by HR staff, who
need exact numbers. Its real benefit is outside the history's reach (differencing across different purposes, apps
or tenants, where a one-person change stays hidden unless it crosses a boundary, i.e. in (B-1)/B of cases) for
contracts that print counts without listings. Turn it on (a sealed, signed change) when that matters more than exact
numbers. Calibrated noise (differential privacy, e.g. OpenDP) is the future option for released statistics; it is
not built in this round.

**Why deterministic thresholds and not differential privacy.** These outputs are counts and lists that a person reads
and acts on. An exception list must name the right employees, and "how many accounts are disabled" must be either
the true number or a visible suppression. Calibrated noise (OpenDP) would make the numbers wrong without saying which
ones. Optimal anonymisation (ARX) addresses a different task: publishing a whole microdata table. For counts that a
person reads, the standard tool is a k-threshold with complementary suppression, as statistical offices use it
(e.g. tau-ARGUS). It needs no new dependency and can be audited exactly. Differential privacy remains a future
option for statistics that are released rather than acted on. It is not built.

**What it does not do.** The protection is partial; SECURITY_BOUNDARY.md §12 lists what remains:
- Inference from the provider's background knowledge.
- Attribute disclosure when all k people of a group share the same value. Example: "all 6 people matching
  `department = Legal` are disabled". l-diversity is not enforced.
- Differencing across different tenants or dataset identities (another schema, another dataset label), or across
  apps / purposes the sealed config separates: each has its own history.
- A small union of atoms that is neither an answered cell nor linearly determined, pinned only through marker
  bounds (R2 certifies cells and atoms); the response pattern itself (which cells were suppressed).
- Records whose identity changes between exports beyond what normalisation absorbs count as a leaver plus a joiner.
- Answers recorded outside the history (a deployment pointed at another store file), or forgotten by an operator's
  `history recover --new`.
- Exception lists and code counts of payroll, timecard and contractor contracts are not part of the history (they
  describe the listed people by design).
- Reasoning about the suppression algorithm itself.
- The record total is the size of the export the caller chose, not a quasi-identifier cell. It is printed even
  below k unless complementary suppression needs it.

## Requests

```json
{"model": "...", "messages": [{"role": "user", "content": "{\"source\": \"india_payroll_register\", \"format\": \"csv\", \"data\": \"Employee Code,...\\n...\"}"}]}
```

Envelope keys: `source`, `format`, `data` (JSON value, or CSV/XML text), optional `params`, `delimiter`
(`,` `;` tab `|`). Headers as for every contracted purpose, including the source attestation over the messages.
Errors are 4xx with content-free messages (row numbers and schema field names only). Large inputs: the payload is
parsed in a bounded worker under the contract's deadline; JSON body parsing and the attestation digest run on the
event loop (a 20 MB body costs a few hundred ms there).

**Chunking and alias consistency.** The gateway has a conversation scope: aliases are an HMAC of the value under a
key derived per tenant/contract/conversation (`X-PGW-Conversation-Id`). Send the chunks of one large export with
the same conversation id and the same person gets the same alias in every chunk's prompt and answer
(`test_aliases_are_consistent_across_chunks_of_one_conversation`); without an id each request gets fresh aliases.
Counts are per chunk; aggregating them is the caller's job. Disclosure-control history is per tenant, app, purpose
and dataset, not per conversation: the counts of every chunk are compared with every earlier count of that history,
so two chunks whose matching sets differ by fewer than k records are refused as a differencing pair.

## What this route still cannot do

- It cannot vouch for a schema that misnames a field: a special category under an innocuous vendor name
  (`customString7` holding religion) is safe only because unknown → NEVER; if a schema author maps it as `REMOVE`
  it is still dropped, but if they map it as `KEEP` with a matching enumeration, it leaves. Review schemas like
  code.
- Free text **inside** a selected field (a comment column) is never selectable as anything but REMOVE/NEVER; there
  is no "summarise these comments" purpose here (use the free-text path, with its limits).
- Small groups are controlled by a deterministic k-threshold (see "Small groups"). The protection is partial: it
  does not cover background knowledge, homogeneous groups (l-diversity), or differencing across conversations,
  processes or exports.
- The TDS checks are sanity flags, not a tax computation (regime, declarations, perquisites, arrears unknown).
  ESIC contribution-period continuation is flagged for review, not resolved. PT schedules are starters.
- Excel workbooks are not parsed (export CSV). XML is supported only via the standard library's expat with every
  DTD refused.
- No workspace UI task yet: the HR Workspace still sends only the existing forms (see the report).
