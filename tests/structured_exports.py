"""Seeded generator of synthetic HR record exports and API payloads, for tests that assert PROTECTION.

Formats: delimited (comma, tab, pipe, semicolon; quoted CSV), markdown and ASCII tables, fixed-width exports (with
and without a header, gap- or single-space separated, ALL-CAPS names), JSON (compact, pretty, wrapped in a report
envelope, truncated, NDJSON, one nested worker object), XML (SOAP-like element records, attribute rosters), LDIF,
YAML lists of mappings, INI sections, logfmt lines. Field names mix vendor-neutral and vendor-like spellings, column
order and casing vary, and every export is either pasted alone or embedded in ticket prose.

Every value is synthetic (syllable names, random digits). `Case.values` lists the values that must never reach an
external provider: names, ids, amounts, addresses, PIN codes, user names, rates and leave/medical codes.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass

_SYL = "ka ra ma na sha ta vi pri ya de su re la ni ga ji mo ha an ru pa ve ki du so bha go le".split()
_STREETS = ("MG Road", "Link Road", "Station Road", "Nehru Nagar", "Gandhi Chowk", "Lake View Layout")
_CITIES = ("Pune", "Nagpur", "Indore", "Mysuru", "Kochi", "Surat")
_CODES = ("Medical Leave - Oncology", "SICK - Medical", "Maternity Leave", "at dialysis", "criminal record check",
          "Bereavement", "LOP - unauthorised")

# field kind -> spellings used as headers/keys (vendor-neutral and vendor-like)
FIELDS = {
    "id": ("EMP_ID", "Employee ID", "Worker_ID", "employeeNumber", "EMP NO", "personIdExternal", "Emp Code", "uid"),
    "name": ("Name", "EMPLOYEE NAME", "Worker", "full_name", "displayName", "Legal_Name", "EMP_NAME", "cn"),
    "manager": ("Manager", "MANAGER_NAME", "managerName", "Reports_To", "Supervisor"),
    "basic": ("BASIC", "Basic Pay", "basePay", "Base_Salary", "Monthly Basic"),
    "gross": ("Gross", "GROSS", "grossPay", "Gross_Amount", "Total Earnings"),
    "rate": ("Hourly Rate", "bill_rate", "PAY_RATE", "hourlyRate"),
    "street": ("Address", "street", "Address_Line_1", "homeAddress"),
    "pin": ("PIN", "postalCode", "Pin Code", "ZIP"),
    "user": ("sAMAccountName", "username", "login", "AD_USER"),
    "code": ("Absence Type", "leaveReason", "Time_Off_Type", "REMARKS"),
    "date": ("DOJ", "Date of Joining", "hireDate", "Start_Date"),
    "pf": ("UAN", "uan_no", "PF Number", "ESIC_NO"),
}


def _name(r: random.Random) -> str:
    w = lambda: "".join(r.choice(_SYL) for _ in range(r.randint(2, 3))).capitalize()  # noqa: E731
    n = f"{w()} {w()}"
    return n.upper() if r.random() < 0.45 else n


def _value(r: random.Random, kind: str) -> str:
    if kind == "id":
        return r.choice([str(r.randint(10000, 99999)), f"EMP{r.randint(1000, 99999)}", f"W-{r.randint(100000, 999999)}"])
    if kind in ("name", "manager"):
        return _name(r)
    if kind in ("basic", "gross"):
        return r.choice([f"{r.randint(15000, 99999)}.{r.randint(0, 99):02d}", str(r.randint(15000, 250000)),
                         f"{r.randint(15, 99)},{r.randint(100, 999)}.00"])
    if kind == "rate":
        return f"{r.randint(350, 4800)}.{r.randint(0, 99):02d}"
    if kind == "street":
        return f"{r.randint(1, 450)} {r.choice(_STREETS)}"
    if kind == "pin":
        return str(r.randint(110001, 855999))
    if kind == "user":
        return f"{r.choice(_SYL)}{r.choice(_SYL)}{r.randint(10, 99)}"
    if kind == "code":
        return r.choice(_CODES)
    if kind == "date":
        return f"{r.randint(2001, 2025)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"
    if kind == "pf":
        return str(r.randint(10**11, 10**12 - 1))
    raise KeyError(kind)


# values that are long enough to search for unambiguously in a payload (a 2-digit number would match anything)
def _searchable(v: str) -> bool:
    return len(v) >= 5


@dataclass(frozen=True)
class Case:
    fmt: str
    text: str
    values: tuple[str, ...]
    records: int
    embedded: bool


FORMATS = ("csv", "csv_quoted", "tsv", "pipe", "semicolon", "markdown", "ascii", "fixed_header", "fixed_noheader",
           "single_space", "json_array", "json_pretty", "json_wrapped", "json_truncated", "ndjson", "json_object",
           "xml_elements", "xml_attrs", "ldif", "yaml", "ini", "logfmt")
_SINGLE = {"json_object", "ini"}       # one record per export


def _schema(r: random.Random, fmt: str) -> list[tuple[str, str]]:
    kinds = ["id", "name"] + r.sample([k for k in FIELDS if k not in ("id", "name")], r.randint(2, 5))
    if fmt in ("single_space", "fixed_noheader"):
        # a header-less, space separated export: text columns cannot be told apart, so amounts and ids follow names
        kinds = ["id", "name"] + r.sample(["basic", "gross", "rate", "pin", "pf", "date"], r.randint(2, 4))
    elif r.random() < 0.5:
        r.shuffle(kinds)
    if fmt in _SINGLE and len(kinds) < 7:
        kinds += [k for k in ("street", "pin", "manager", "basic", "user", "date") if k not in kinds][: 7 - len(kinds)]
    keyish = fmt.startswith(("json", "xml", "ldif", "yaml", "ini", "logfmt", "ndjson"))
    out = []
    for k in kinds:
        names = FIELDS[k]
        if keyish:
            names = tuple(n for n in names if " " not in n) or (names[0].replace(" ", "_"),)
        h = r.choice(names)
        out.append((k, h.upper() if r.random() < 0.2 and not keyish else h))
    return out


def _rows(r: random.Random, schema, n: int) -> list[list[str]]:
    return [[_value(r, k) for k, _ in schema] for _ in range(n)]


def _quote(v: str, d: str, always: bool) -> str:
    return f'"{v}"' if always or d in v or " " in v and d == "," else v


def _render(r: random.Random, fmt: str, schema, rows) -> str:
    heads = [h for _, h in schema]
    if fmt in ("csv", "csv_quoted", "tsv", "pipe", "semicolon"):
        d = {"csv": ",", "csv_quoted": ",", "tsv": "\t", "pipe": "|", "semicolon": ";"}[fmt]
        q = fmt == "csv_quoted"
        lines = [d.join(_quote(c, d, q) if d in ",;" else c for c in row) for row in rows]
        if r.random() < 0.85:
            lines.insert(0, d.join(heads))
        return "\n".join(lines)
    if fmt == "markdown":
        out = ["| " + " | ".join(heads) + " |", "|" + "|".join("---" for _ in heads) + "|"]
        return "\n".join(out + ["| " + " | ".join(row) + " |" for row in rows])
    if fmt == "ascii":
        w = [max(len(x[i]) for x in [heads] + rows) for i in range(len(heads))]
        rule = "+" + "+".join("-" * (n + 2) for n in w) + "+"
        fmt_row = lambda row: "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(row)) + " |"  # noqa: E731
        return "\n".join([rule, fmt_row(heads), rule] + [fmt_row(x) for x in rows] + [rule])
    if fmt in ("fixed_header", "fixed_noheader"):
        w = [max(len(x[i]) for x in [heads] + rows) + r.randint(2, 4) for i in range(len(heads))]
        lines = ([heads] if fmt == "fixed_header" else []) + rows
        return "\n".join("".join(c.ljust(w[i]) for i, c in enumerate(x)).rstrip() for x in lines)
    if fmt == "single_space":
        lines = [" ".join(x) for x in rows]
        if r.random() < 0.5:
            lines.insert(0, "  ".join(h.upper() for h in heads))
        return "\n".join(lines)
    recs = [dict(zip(heads, row, strict=True)) for row in rows]
    if fmt == "json_array":
        return json.dumps(recs, ensure_ascii=False)
    if fmt == "json_pretty":
        return json.dumps(recs, indent=r.choice([2, 4]), ensure_ascii=False)
    if fmt == "json_wrapped":
        env = r.choice(["Report_Entry", "value", "results", "data", "workers"])
        return json.dumps({"@odata.context": "$metadata#x", env: recs, "total": len(recs)}, indent=2, ensure_ascii=False)
    if fmt == "json_truncated":
        ind = r.choice([None, 2])
        s = json.dumps({"items": recs}, indent=ind, ensure_ascii=False)
        last = len(json.dumps(recs[-1], indent=ind, ensure_ascii=False))
        return s[:len(s) - r.randint(5, max(6, last // 2))]      # cut inside the last record
    if fmt == "ndjson":
        return "\n".join(json.dumps(x, ensure_ascii=False) for x in recs)
    if fmt == "json_object":
        rec = recs[0]
        keys = list(rec)
        nested = {keys[0]: rec[keys[0]], "personal": {k: rec[k] for k in keys[1:4]},
                  "employment": {k: rec[k] for k in keys[4:]}}
        return json.dumps({"worker": nested}, indent=r.choice([None, 2]), ensure_ascii=False)
    if fmt == "xml_elements":
        ns = r.choice(["wd:", "", "ns1:"])
        tag = r.choice(["Worker", "Employee", "row", "Entry"])
        body = "".join(f"<{ns}{tag}>" + "".join(f"<{ns}{h}>{v}</{ns}{h}>" for h, v in x.items()) + f"</{ns}{tag}>"
                       for x in recs)
        if r.random() < 0.5:
            body = body.replace("><", ">\n<")
        return (f'<?xml version="1.0"?><env:Envelope xmlns:env="urn:x"><env:Body><{ns}Response_Data>' + body +
                f"</{ns}Response_Data></env:Body></env:Envelope>")
    if fmt == "xml_attrs":
        return "<roster>\n" + "\n".join("  <person " + " ".join(f'{h}="{v}"' for h, v in x.items()) + "/>"
                                         for x in recs) + "\n</roster>"
    if fmt == "ldif":
        return "\n\n".join(f"dn: uid={i},ou=people,dc=example,dc=in\n" + "\n".join(f"{h}: {v}" for h, v in x.items())
                           for i, x in enumerate(recs))
    if fmt == "yaml":
        out = ["employees:"]
        for x in recs:
            items = list(x.items())
            out.append(f"  - {items[0][0]}: {items[0][1]}")
            out += [f"    {h}: {v}" for h, v in items[1:]]
        return "\n".join(out)
    if fmt == "ini":
        rec = recs[0]
        keys = list(rec)
        return ("[identity]\n" + "\n".join(f"{k} = {rec[k]}" for k in keys[:3]) + "\n\n[employment]\n" +
                "\n".join(f"{k} = {rec[k]}" for k in keys[3:]))
    if fmt == "logfmt":
        return "\n".join(f"ts=2025-0{r.randint(1, 9)}-1{r.randint(0, 9)}T10:0{r.randint(0, 9)}:00Z level=info " +
                         " ".join(f'{h}="{v}"' if " " in v else f"{h}={v}" for h, v in x.items()) for x in recs)
    raise KeyError(fmt)


_BEFORE = ("Hi team,\nplease check the export below and draft a note to the employees about the correction.\n\n",
           "Payroll queries for this month (copied from the system):\n",
           "FYI - pasted from the HRMS. Can you summarise what changed?\n\n")
_AFTER = ("\n\nThanks,\nHR Ops", "\n\nPlease revert by EOD.", "\n")


def case(seed: int, fmt: str | None = None, records: int | None = None) -> Case:
    r = random.Random(seed)
    fmt = fmt or FORMATS[seed % len(FORMATS)]
    n = 1 if fmt in _SINGLE else (records or r.choice([2, 2, 3, 4, 7, 12, 30, 80, 200, 500]))
    schema = _schema(r, fmt)
    rows = _rows(r, schema, n)
    text = _render(r, fmt, schema, rows)
    embedded = r.random() < 0.5
    if embedded:
        text = r.choice(_BEFORE) + text + r.choice(_AFTER)
    shown = text
    values = tuple(sorted({v for row in rows for v in row if _searchable(v) and v in shown}))
    return Case(fmt, text, values, n, embedded)


def cases(n: int, seed: int = 20261028, max_records: int | None = None) -> list[Case]:
    out = []
    for i in range(n):
        c = case(seed + i)
        if max_records is not None and c.records > max_records:
            c = case(seed + i, records=max_records)
        out.append(c)
    return out
