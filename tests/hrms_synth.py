"""Seeded generators of SYNTHETIC HRMS exports for the field-contract tests (tests/test_hrms_records.py).

Every person, number and identifier here is invented. Each generator returns a Synth: the payload for the RECORDS
envelope plus, for the assertions, every value it emitted that the schema does NOT classify KEEP (`secrets`), the
words of special-category values (`never_words`), unknown-field canaries, and the people it listed.
IDs carry valid shapes and checksums where a scheme has one (Aadhaar: Verhoeff; PAN: AAAAA9999A with P for a person).
"""
from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from pgw.detect.recognizers import verhoeff_check_digit

FIRST = ["Asha", "Imran", "Priya", "Vikram", "Neha", "Rahul", "Kavya", "Arjun", "Deepa", "Suresh", "Lakshmi",
         "Farhan", "Meera", "Ravi", "Anil", "Sunita", "Gurpreet", "Thomas", "Joseph", "Fatima", "Sandeep", "Ishita"]
LAST = ["Raghavan", "Ansari", "Iyer", "Nair", "Shah", "Reddy", "Kulkarni", "Banerjee", "Menon", "Pillai",
        "Chatterjee", "Deshpande", "Gill", "Fernandes", "Dsouza", "Khan", "Verma", "Mehta", "Bhat", "Hegde"]
DEPTS = ["Finance", "Engineering", "Operations", "Sales", "Marketing", "Human Resources", "Legal", "Support"]
STATES = ["KA", "MH", "WB", "TS", "GJ", "Karnataka", "Maharashtra", "DL", "TN"]
CITIES = [("Bengaluru", "5600"), ("Mumbai", "4000"), ("Kolkata", "7000"), ("Hyderabad", "5000"), ("Ahmedabad", "3800")]
LEAVE_TYPES = ["Maternity Leave", "Oncology OPD", "Dialysis", "Sick Leave", "Miscarriage Leave", "Psychiatric Care",
               "Paternity Leave", "Bereavement"]
GENDERS = ["Female", "Male", "Transgender"]
MARITAL = ["Married", "Divorced", "Widowed", "Unmarried"]
NATIONALITY = ["Nepalese", "Bhutanese", "Indian", "Sri Lankan"]
RELIGION = ["Hindu", "Muslim", "Christian", "Sikh", "Buddhist", "Jain"]
CASTE = ["Scheduled Caste", "Scheduled Tribe", "OBC"]
DISABILITY = ["Locomotor Disability", "Hearing Impairment", "Visual Impairment"]
BGV = ["Criminal Record Check: adverse finding", "Pending court case", "Verified clean", "Address mismatch"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Synth:
    schema: str
    fmt: str
    data: object
    secrets: list = field(default_factory=list)       # (kind, value): every non-KEEP value emitted
    never_words: set = field(default_factory=set)     # words of NEVER values (special categories)
    canaries: list = field(default_factory=list)      # values of fields the schema does not know
    unknown_paths: set = field(default_factory=set)
    people: list = field(default_factory=list)        # (name, id) of every record

    def envelope(self, params=None) -> str:
        env = {"source": self.schema, "format": self.fmt, "data": self.data}
        if params is not None:
            env["params"] = params
        return json.dumps(env)

    def s(self, kind, v):
        self.secrets.append((kind, v))
        return v

    def never(self, v):
        self.secrets.append(("str", v))
        self.never_words.update(w for w in v.replace(":", " ").split() if len(w) >= 4)
        return v

    def canary(self, path, rng):
        v = f"UNKCANARY{rng.randrange(10**8, 10**9)}"
        self.canaries.append(v)
        self.unknown_paths.add(path)
        return v


def inr(n: int) -> str:
    s = str(abs(n))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail]) if groups else tail


def pan(rng) -> str:
    L = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(rng.choice(L) for _ in range(3)) + "P" + rng.choice(L) + f"{rng.randrange(1000, 9999)}" + rng.choice(L)


def aadhaar(rng) -> str:
    a = str(rng.randrange(2, 10)) + "".join(str(rng.randrange(10)) for _ in range(10))
    a += verhoeff_check_digit(a)
    return f"{a[:4]} {a[4:8]} {a[8:]}"


def name(rng, i: int) -> str:
    n = f"{FIRST[i % len(FIRST)]} {LAST[(i * 7 + rng.randrange(3)) % len(LAST)]}"
    if rng.random() < 0.15:
        n += f" {rng.choice(LAST)}"
    return n.upper() if rng.random() < 0.25 else n            # ALL-CAPS as many registers export


def date_forms(d: dt.date) -> list[str]:
    return [d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%d.%m.%Y"),
            f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}", f"{d.day} {d.strftime('%B')} {d.year}",
            f"{d.strftime('%B')} {d.day}, {d.year}", f"{MONTHS[d.month - 1]} {d.day}, {d.year}"]


def rand_date(rng, y0, y1) -> dt.date:
    return dt.date(rng.randrange(y0, y1), rng.randrange(1, 13), rng.randrange(1, 29))


def odd_amount(rng, lo, hi) -> int:
    """An amount that is not round (so it cannot coincide with a small count or a template number)."""
    x = rng.randrange(lo, hi)
    return x if x % 10 else x + 3


def address(rng) -> tuple[str, str]:
    city, pin = rng.choice(CITIES)
    pin = pin + f"{rng.randrange(10, 99)}"
    return f"Flat {rng.randrange(1, 900)}B, Sai Krupa Residency, {rng.randrange(1, 20)}th Cross, {city}", pin


# ---------------------------------------------------------------------------------------------------------------
# Indian payroll register (CSV)
# ---------------------------------------------------------------------------------------------------------------

PAYROLL_HEADER = ["Employee Code", "Employee Name", "Email", "UAN", "PAN", "Aadhaar No", "Bank A/c No", "IFSC",
                  "Mobile", "Address", "PIN", "Department", "Designation", "State", "Pay Period", "DOB", "DOJ",
                  "Gender", "Marital Status", "Religion", "Category", "Disability", "Leave Type", "LOP Reason",
                  "Days Paid", "LOP Days", "Basic", "HRA", "Conveyance", "Special Allowance", "Gross", "PF", "ESIC",
                  "PT", "TDS", "Other Deductions", "Net Pay", "Remarks", "Blood Group", "Emergency Contact"]


def _pt(state: str, gross: int, feb: bool) -> int:
    code = {"Karnataka": "KA", "Maharashtra": "MH"}.get(state, state)
    if code == "KA":
        return (300 if feb else 200) if gross >= 25000 else 0
    if code == "MH":
        amt = 0 if gross <= 7500 else 175 if gross <= 10000 else 200
        return 300 if feb and amt == 200 else amt
    if code == "WB":
        return 0 if gross <= 10000 else 110 if gross <= 15000 else 130 if gross <= 25000 else 150 if gross <= 40000 else 200
    if code == "TS":
        return 0 if gross <= 15000 else 150 if gross <= 20000 else 200
    if code == "GJ":
        return 200 if gross >= 12000 else 0
    return 0


def payroll(n: int, seed: int = 7, error_rate: float = 0.2, fmt: str = "csv", period=(2026, 2)) -> Synth:
    rng = random.Random(seed)
    sy = Synth("india_payroll_register", fmt, None)
    rows = []
    feb = period[1] == 2
    for i in range(n):
        emp = f"KD-{10000 + i * 7 + rng.randrange(5)}"
        nm = name(rng, i)
        sy.people.append((nm, emp))
        basic = odd_amount(rng, 9000, 140000)
        hra = basic * 2 // 5 + 1
        conv = 1600 if rng.random() < 0.5 else 0
        spl = odd_amount(rng, 1000, 60000)
        gross = basic + hra + conv + spl
        pf = round(0.12 * min(basic, 15000)) if rng.random() < 0.8 else round(0.12 * basic)
        import math
        esic = math.ceil(0.0075 * gross) if gross <= 21000 else 0
        state = rng.choice(STATES)
        pt = _pt(state, gross, feb)
        annual = gross * 12 - 75000
        tds = 0 if annual <= 1200000 else odd_amount(rng, 1000, max(1001, gross // 5))
        other = 0
        net = gross - pf - esic - pt - tds - other
        err = rng.random() < error_rate
        if err:
            kind = rng.randrange(4)
            if kind == 0:
                net += 117                     # NET_MISMATCH
            elif kind == 1:
                pf += 213                      # PF_MISMATCH
            elif kind == 2:
                gross += 1003                  # GROSS_MISMATCH (+ derived)
            else:
                tds = 2731 if annual <= 1200000 else tds
        addr, pin = address(rng)
        dob, doj = rand_date(rng, 1965, 2003), rand_date(rng, 2008, 2026)
        uan = f"10{rng.randrange(10**9, 10**10)}"
        acct = str(rng.randrange(10**10, 10**12))
        email = nm.lower().replace(" ", ".") + "@kestrel-dyn.example"
        mobile = f"9{rng.randrange(10**8, 10**9)}"
        leave = rng.choice(LEAVE_TYPES) if rng.random() < 0.3 else ""
        rec = {
            "Employee Code": sy.s("str", emp), "Employee Name": sy.s("str", nm), "Email": sy.s("str", email),
            "UAN": sy.s("str", uan), "PAN": sy.s("str", pan(rng)), "Aadhaar No": sy.s("str", aadhaar(rng)),
            "Bank A/c No": sy.s("str", acct), "IFSC": sy.s("str", f"HDFC0{rng.randrange(10**5, 10**6)}"),
            "Mobile": sy.s("str", mobile), "Address": sy.s("str", addr), "PIN": sy.s("str", pin),
            "Department": rng.choice(DEPTS), "Designation": sy.s("str", rng.choice(["Senior Analyst", "Deputy Manager", "Associate Consultant"])),
            "State": state, "Pay Period": f"{period[0]}-{period[1]:02d}-01" if i % 2 else f"01-{MONTHS[period[1] - 1]}-{period[0]}",
            "DOB": sy.s("date", dob).strftime("%d/%m/%Y"), "DOJ": sy.s("date", doj).isoformat(),
            "Gender": sy.never(rng.choice(GENDERS)), "Marital Status": sy.never(rng.choice(MARITAL)),
            "Religion": sy.never(rng.choice(RELIGION)), "Category": sy.never(rng.choice(CASTE)),
            "Disability": sy.never(rng.choice(DISABILITY)) if rng.random() < 0.1 else "",
            "Leave Type": sy.never(leave) if leave else "", "LOP Reason": sy.never("Chemotherapy cycle") if leave else "",
            "Days Paid": "28", "LOP Days": "0",
            "Basic": inr(sy.s("amount", basic)) if i % 3 == 0 else str(sy.s("amount", basic)),
            "HRA": str(sy.s("amount", hra)), "Conveyance": str(conv) if conv else "",
            "Special Allowance": f"{sy.s('amount', spl):,}", "Gross": str(sy.s("amount", gross)),
            "PF": str(sy.s("amount", pf)), "ESIC": str(sy.s("amount", esic)), "PT": str(pt),
            "TDS": str(sy.s("amount", tds)), "Other Deductions": "", "Net Pay": f"{sy.s('amount', net)}.00",
            "Remarks": sy.s("str", f"Arrears adjusted for {rng.choice(['Q3 bonus', 'relocation', 'notice recovery'])}"),
            "Blood Group": sy.canary("Blood Group", rng), "Emergency Contact": sy.canary("Emergency Contact", rng),
        }
        rows.append(rec)
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=PAYROLL_HEADER)
        w.writeheader()
        w.writerows(rows)
        sy.data = buf.getvalue()
    else:
        sy.data = rows
        sy.unknown_paths = {"[].Blood Group", "[].Emergency Contact"}
    return sy


# ---------------------------------------------------------------------------------------------------------------
# UKG / Kronos timecard (CSV)
# ---------------------------------------------------------------------------------------------------------------

def timecard(n_people: int, seed: int = 11, days: int = 14) -> Synth:
    rng = random.Random(seed)
    sy = Synth("ukg_timecard", "csv", None)
    rows = []
    start = dt.date(2026, 3, 2)   # a Monday
    for i in range(n_people):
        emp, nm = f"{700000 + i * 13}", name(rng, i)
        sy.people.append((nm, emp))
        mgr = name(rng, i + 5)
        for d in range(days):
            day = start + dt.timedelta(days=d)
            if day.weekday() == 6:
                continue
            row = {"Person Number": sy.s("str", emp), "Full Name": sy.s("str", nm), "Manager": sy.s("str", mgr),
                   "Apply Date": sy.s("date", day).strftime("%d/%m/%Y"), "Department": rng.choice(DEPTS),
                   "Location": sy.s("str", "Whitefield Campus Block C"),
                   "Comments": sy.s("str", "Came late due to hospital visit for chemo"),
                   "Badge Colour": sy.canary("Badge Colour", rng)}
            r = rng.random()
            if r < 0.06:
                lt = rng.choice(LEAVE_TYPES)
                row.update({"Pay Code": sy.never(lt), "Absence Reason": sy.never("Chemotherapy session"),
                            "In Punch": "", "Out Punch": "", "Hours": ""})
            else:
                h_in = 9 if rng.random() < 0.9 else 7
                m_in = rng.randrange(0, 30)
                length = 8.5 if rng.random() < 0.85 else 11.25
                out_min = h_in * 60 + m_in + int(length * 60)
                row.update({"Pay Code": "REG", "Absence Reason": "",
                            "In Punch": f"{h_in:02d}:{m_in:02d}",
                            "Out Punch": f"{out_min // 60 % 24:02d}:{out_min % 60:02d}" if rng.random() > 0.04 else "",
                            "Hours": f"{length - 0.5:.2f}"})
                if length > 9:
                    row["Overtime Hours"] = f"{length - 9.5:.2f}"
                    row["OT Approved"] = rng.choice(["Y", "N"])
            row.setdefault("Overtime Hours", "")
            row.setdefault("OT Approved", "")
            rows.append(row)
    import csv
    import io
    buf = io.StringIO()
    fields_ = ["Person Number", "Full Name", "Manager", "Apply Date", "In Punch", "Out Punch", "Hours", "Overtime Hours",
               "OT Approved", "Pay Code", "Absence Reason", "Department", "Location", "Comments", "Badge Colour"]
    w = csv.DictWriter(buf, fieldnames=fields_)
    w.writeheader()
    w.writerows(rows)
    sy.data = buf.getvalue()
    return sy


# ---------------------------------------------------------------------------------------------------------------
# SAP Fieldglass worker (CSV)
# ---------------------------------------------------------------------------------------------------------------

def fieldglass(n: int, seed: int = 13) -> Synth:
    rng = random.Random(seed)
    sy = Synth("sap_fieldglass_worker", "csv", None)
    rows = []
    for i in range(n):
        wid, nm = f"FGW{4000000 + i * 31}", name(rng, i)
        sy.people.append((nm, wid))
        first, last = nm.split(" ", 1)
        budget = odd_amount(rng, 300000, 4000000)
        spend = int(budget * rng.choice([0.3, 0.7, 0.93, 1.04, 1.3])) + 7
        appr = odd_amount(rng, 900, 6000)
        rate = appr if rng.random() < 0.8 else appr + rng.randrange(50, 1500)
        addr, _pin = address(rng)
        rows.append({
            "Worker ID": sy.s("str", wid), "Worker Name": sy.s("str", nm), "First Name": sy.s("str", first),
            "Last Name": sy.s("str", last), "Worker Email": sy.s("str", f"w{i}.{last.lower()}@supplier.example"),
            "Work Order ID": sy.s("str", f"WO-{880000 + i}"), "PO Number": sy.s("str", f"PO45{rng.randrange(10**6, 10**7)}"),
            "Work Order Owner": sy.s("str", name(rng, i + 3)), "Supplier": sy.s("str", "Brightpath Staffing Pvt Ltd"),
            "Job Posting Title": sy.s("str", "Contract Data Engineer"), "Site": sy.s("str", "Pune Hinjewadi Phase 2"),
            "Phone": sy.s("str", f"98{rng.randrange(10**7, 10**8)}"), "Home Address": sy.s("str", addr),
            "PAN": sy.s("str", pan(rng)), "Bill Rate": f"{sy.s('amount', rate):,}.00",
            "Approved Bill Rate": str(sy.s("amount", appr)), "Pay Rate": str(sy.s("amount", rate - 211)),
            "Work Order Budget": inr(sy.s("amount", budget)), "Spend to Date": str(sy.s("amount", spend)),
            "Hours Billed": "312.5", "Currency": "INR", "Cost Center": rng.choice(DEPTS),
            "Start Date": sy.s("date", rand_date(rng, 2024, 2026)).isoformat(),
            "End Date": sy.s("date", rand_date(rng, 2026, 2028)).strftime("%d-%m-%Y"),
            "Date of Birth": sy.s("date", rand_date(rng, 1970, 2000)).strftime("%d/%m/%Y"),
            "Worker Status": "Active", "Background Check Status": sy.never(rng.choice(["Initiated", "Escalated"])),
            "Background Check Result": sy.never(rng.choice(BGV)), "Gender": sy.never(rng.choice(GENDERS)),
            "Nationality": sy.never(rng.choice(NATIONALITY)), "Union Affiliation": sy.canary("Union Affiliation", rng),
        })
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    sy.data = buf.getvalue()
    return sy


# ---------------------------------------------------------------------------------------------------------------
# Active Directory users (JSON, as ConvertTo-Json)
# ---------------------------------------------------------------------------------------------------------------

def ad_users(n: int, seed: int = 17, fmt: str = "json") -> Synth:
    rng = random.Random(seed)
    sy = Synth("ad_user", fmt, None)
    out = []
    for i in range(n):
        nm = name(rng, i)
        first, last = nm.split(" ", 1)
        user = (first[0] + last.split(" ")[0]).lower() + str(i)
        dn = f"CN={nm},OU=Staff,DC=corp,DC=kestrel,DC=example"
        mgr = f"CN={name(rng, i + 9)},OU=Staff,DC=corp,DC=kestrel,DC=example"
        created = dt.datetime(2020 + i % 6, 1 + i % 12, 1 + i % 27, 9, 30)
        last_logon = dt.datetime(2026, 1 + i % 9, 1 + i % 27, 10, 15)
        ft = int((last_logon - dt.datetime(1601, 1, 1)).total_seconds() * 10**7)
        sy.people.append((nm, user))
        _addr, pin = address(rng)
        rec = {"sAMAccountName": sy.s("str", user), "userPrincipalName": sy.s("str", f"{user}@corp.kestrel.example"),
               "displayName": sy.s("str", nm), "givenName": sy.s("str", first), "sn": sy.s("str", last),
               "cn": sy.s("str", nm), "name": sy.s("str", nm), "mail": sy.s("str", f"{user}@kestrel.example"),
               "employeeID": sy.s("str", f"E{50000 + i}"), "distinguishedName": sy.s("str", dn),
               "manager": sy.s("str", mgr), "department": DEPTS[i % len(DEPTS)], "Enabled": i % 5 != 0,
               "userAccountControl": 514 if i % 5 == 0 else 512,
               "whenCreated": created.strftime("%Y%m%d%H%M%S.0Z"), "lastLogonTimestamp": ft,
               "postalCode": sy.s("str", pin), "title": sy.s("str", "Regional Payroll Lead"),
               "telephoneNumber": sy.s("str", f"+91 80 {rng.randrange(1000, 9999)} {rng.randrange(1000, 9999)}"),
               "mobile": sy.s("str", f"+91 9{rng.randrange(10**8, 10**9)}"),
               "streetAddress": sy.s("str", "221 Residency Road Ashok Nagar"), "l": sy.s("str", "Bengaluru"),
               "memberOf": [sy.s("str", "CN=Cancer Survivors Network,OU=Groups,DC=corp,DC=kestrel,DC=example"),
                            sy.s("str", "CN=Staff Union Committee,OU=Groups,DC=corp,DC=kestrel,DC=example")],
               "thumbnailPhoto": sy.never("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg"),
               "objectSid": sy.s("str", f"S-1-5-21-3623811015-3361044348-30300820-{1013 + i}"),
               "extensionAttribute9": sy.canary("[].extensionAttribute9", rng)}
        sy.secrets.append(("date", created.date()))
        out.append(rec)
    sy.data = out
    return sy


# ---------------------------------------------------------------------------------------------------------------
# Workday REST workers collection (JSON)
# ---------------------------------------------------------------------------------------------------------------

def _wid(rng) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(32))


def workday_rest(n: int, seed: int = 19) -> Synth:
    rng = random.Random(seed)
    sy = Synth("workday_worker_rest", "json", None)
    data = []
    for i in range(n):
        nm = name(rng, i)
        eid = f"21{rng.randrange(1000, 9999)}{i}"
        sy.people.append((nm, eid))
        mgr = name(rng, i + 4)
        hire = rand_date(rng, 2010, 2026)
        dob = rand_date(rng, 1965, 2003)
        _a, pin = address(rng)
        data.append({
            "id": sy.s("str", _wid(rng)), "workerId": sy.s("str", eid), "descriptor": sy.s("str", nm),
            "href": sy.s("str", f"https://wd5-impl.example/ccx/api/v1/tenant/workers/{_wid(rng)}"),
            "primaryWorkEmail": sy.s("str", nm.lower().replace(" ", ".") + "@kestrel.example"),
            "primaryWorkPhone": sy.s("str", f"+91 20 {rng.randrange(1000, 9999)} {rng.randrange(1000, 9999)}"),
            "businessTitle": sy.s("str", "Principal Payroll Specialist"),
            "primarySupervisoryOrganization": {"id": sy.s("str", _wid(rng)), "descriptor": sy.s("str", f"Payroll Ops ({mgr})")},
            "location": {"descriptor": sy.s("str", "Pune Kharadi Tower 3")},
            "manager": {"descriptor": sy.s("str", mgr), "id": sy.s("str", _wid(rng))},
            "isManager": i % 4 == 0, "workerType": {"descriptor": "Employee", "id": sy.s("str", _wid(rng))},
            "timeType": {"descriptor": "Full time", "id": sy.s("str", _wid(rng))},
            "hireDate": sy.s("date", hire).isoformat(), "dateOfBirth": sy.s("date", dob).isoformat(),
            "homeAddress": {"line1": sy.s("str", "44 Koregaon Park Lane 7"), "postalCode": sy.s("str", pin)},
            "nationalId": {"type": "PAN", "value": sy.s("str", pan(rng))},
            "gender": {"descriptor": sy.never(rng.choice(GENDERS)), "id": sy.s("str", _wid(rng))},
            "maritalStatus": {"descriptor": sy.never(rng.choice(MARITAL))},
            "nationality": {"descriptor": sy.never(rng.choice(NATIONALITY))},
            "religion": {"descriptor": sy.never(rng.choice(RELIGION))},
            "disabilityStatus": [{"descriptor": sy.never(rng.choice(DISABILITY))}],
            "onLeave": {"descriptor": sy.never(rng.choice(LEAVE_TYPES))},
            "favouriteCanteenItem": sy.canary("data[].favouriteCanteenItem", rng),
        })
    sy.data = {"total": n, "data": data, "requestMeta": sy.canary("requestMeta", rng)}
    return sy


# ---------------------------------------------------------------------------------------------------------------
# Workday Get_Workers (SOAP XML)
# ---------------------------------------------------------------------------------------------------------------

def workday_xml(n: int, seed: int = 23) -> Synth:
    rng = random.Random(seed)
    sy = Synth("workday_get_workers", "xml", None)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/"><env:Body>',
             '<wd:Get_Workers_Response xmlns:wd="urn:com.workday/bsvc" wd:version="v42.0">',
             '<wd:Response_Results><wd:Total_Results>%d</wd:Total_Results></wd:Response_Results>' % n,
             '<wd:Response_Data>']
    for i in range(n):
        nm = name(rng, i)
        first, last = nm.split(" ", 1)
        eid = f"3{rng.randrange(10000, 99999)}{i}"
        sy.people.append((nm, eid))
        wid = sy.s("str", _wid(rng))
        mgr = sy.s("str", _wid(rng))
        dob, hire = rand_date(rng, 1965, 2003), rand_date(rng, 2010, 2026)
        sy.s("date", dob), sy.s("date", hire)
        addr, pin = address(rng)
        e = lambda v: escape(v)   # noqa: E731
        parts.append(
            "<wd:Worker>"
            f'<wd:Worker_Reference><wd:ID wd:type="WID">{wid}</wd:ID>'
            f'<wd:ID wd:type="Employee_ID">{e(sy.s("str", eid))}</wd:ID></wd:Worker_Reference>'
            f"<wd:Worker_Descriptor>{e(sy.s('str', nm))}</wd:Worker_Descriptor>"
            "<wd:Worker_Data>"
            f"<wd:Worker_ID>{eid}</wd:Worker_ID><wd:User_ID>{e(sy.s('str', first.lower() + str(i) + 'x'))}</wd:User_ID>"
            "<wd:Personal_Data><wd:Name_Data><wd:Legal_Name_Data>"
            f'<wd:Name_Detail_Data wd:Formatted_Name="{e(nm)}" wd:Reporting_Name="{e(last + ", " + first)}">'
            f"<wd:Country_Reference><wd:ID wd:type=\"ISO_3166-1_Alpha-2_Code\">IN</wd:ID></wd:Country_Reference>"
            f"<wd:First_Name>{e(sy.s('str', first))}</wd:First_Name><wd:Last_Name>{e(sy.s('str', last))}</wd:Last_Name>"
            "</wd:Name_Detail_Data></wd:Legal_Name_Data></wd:Name_Data>"
            f'<wd:Gender_Reference><wd:ID wd:type="Gender_Code">{sy.never(rng.choice(GENDERS))}</wd:ID></wd:Gender_Reference>'
            f"<wd:Birth_Date>{dob.isoformat()}</wd:Birth_Date>"
            f'<wd:Marital_Status_Reference><wd:ID wd:type="Marital_Status_ID">{sy.never(rng.choice(MARITAL))}</wd:ID></wd:Marital_Status_Reference>'
            f'<wd:Religion_Reference><wd:ID wd:type="Religion_ID">{sy.never(rng.choice(RELIGION))}</wd:ID></wd:Religion_Reference>'
            f'<wd:Primary_Nationality_Reference><wd:ID wd:type="ISO">{sy.never(rng.choice(NATIONALITY))}</wd:ID></wd:Primary_Nationality_Reference>'
            f"<wd:Identification_Data><wd:National_ID><wd:ID>{sy.s('str', pan(rng))}</wd:ID></wd:National_ID></wd:Identification_Data>"
            f"<wd:Contact_Data><wd:Address_Data><wd:Address_Line_Data>{e(sy.s('str', addr))}</wd:Address_Line_Data>"
            f"<wd:Postal_Code>{sy.s('str', pin)}</wd:Postal_Code></wd:Address_Data>"
            f"<wd:Email_Address_Data><wd:Email_Address>{e(sy.s('str', first.lower() + '.' + str(i) + '@kestrel.example'))}</wd:Email_Address>"
            "<wd:Usage_Data wd:Public=\"1\"><wd:Type_Data wd:Primary=\"1\"/></wd:Usage_Data></wd:Email_Address_Data>"
            "</wd:Contact_Data>"
            f"<wd:Hobby>{sy.canary('Worker[].Worker_Data.Personal_Data.Hobby', rng)}</wd:Hobby>"
            "</wd:Personal_Data>"
            "<wd:Employment_Data><wd:Worker_Job_Data><wd:Position_Data>"
            f"<wd:Business_Title>{e(sy.s('str', 'Lead Compensation Analyst'))}</wd:Business_Title>"
            f'<wd:Manager_as_of_last_detected_manager_change_Reference><wd:ID wd:type="WID">{mgr}</wd:ID>'
            "</wd:Manager_as_of_last_detected_manager_change_Reference>"
            "<wd:Full_Time_Equivalent_Percentage>100</wd:Full_Time_Equivalent_Percentage>"
            "</wd:Position_Data></wd:Worker_Job_Data>"
            f"<wd:Worker_Status_Data><wd:Active>{'1' if i % 6 else '0'}</wd:Active>"
            f"<wd:Hire_Date>{hire.isoformat()}</wd:Hire_Date>"
            f"<wd:Leave_Status_Data><wd:Leave_Type_Reference><wd:ID>{sy.never(rng.choice(LEAVE_TYPES))}</wd:ID>"
            "</wd:Leave_Type_Reference></wd:Leave_Status_Data></wd:Worker_Status_Data>"
            "</wd:Employment_Data>"
            f"<wd:Background_Check_Data><wd:Status>{sy.never(rng.choice(BGV))}</wd:Status></wd:Background_Check_Data>"
            "</wd:Worker_Data></wd:Worker>")
    parts.append("</wd:Response_Data></wd:Get_Workers_Response></env:Body></env:Envelope>")
    sy.data = "".join(parts)
    return sy


# ---------------------------------------------------------------------------------------------------------------
# SuccessFactors PerPerson OData (JSON)
# ---------------------------------------------------------------------------------------------------------------

def successfactors(n: int, seed: int = 29) -> Synth:
    rng = random.Random(seed)
    sy = Synth("successfactors_employee", "json", None)
    res = []
    for i in range(n):
        nm = name(rng, i)
        first, last = nm.split(" ", 1)
        pid = f"SF{600000 + i * 3}"
        sy.people.append((nm, pid))
        dob, start = rand_date(rng, 1965, 2003), rand_date(rng, 2012, 2026)
        sy.s("date", dob), sy.s("date", start)
        res.append({
            "__metadata": {"uri": sy.s("str", f"https://api4.successfactors.example/odata/v2/PerPerson('{pid}')"),
                           "type": "SFOData.PerPerson"},
            "personIdExternal": sy.s("str", pid), "dateOfBirth": f"/Date({int(dt.datetime(dob.year, dob.month, dob.day).timestamp()) * 1000})/"
            if False else dob.isoformat(),
            "countryOfBirth": sy.never(rng.choice(NATIONALITY)),
            "personalInfoNav": {"results": [{
                "__metadata": {"uri": sy.s("str", f"https://api4.successfactors.example/odata/v2/PerPersonal(personIdExternal='{pid}')")},
                "firstName": sy.s("str", first), "lastName": sy.s("str", last), "displayName": sy.s("str", nm),
                "gender": sy.never(rng.choice(["F", "M", "Female", "Male"])), "maritalStatus": sy.never(rng.choice(MARITAL)),
                "nationality": sy.never(rng.choice(NATIONALITY)), "challengeStatus": sy.never(rng.choice(DISABILITY)),
                "religion": sy.never(rng.choice(RELIGION)), "customString7": sy.canary("customString7", rng)}]},
            "emailNav": {"results": [{"emailAddress": sy.s("str", f"{first.lower()}{i}@kestrel.example"),
                                      "emailType": "B", "isPrimary": True}]},
            "phoneNav": {"results": [{"phoneNumber": sy.s("str", f"98{rng.randrange(10**7, 10**8)}")}]},
            "employmentNav": {"results": [{
                "userId": sy.s("str", f"u{pid.lower()}"), "startDate": start.isoformat(),
                "jobInfoNav": {"results": [{
                    "jobTitle": sy.s("str", "Senior Benefits Partner"), "department": DEPTS[i % len(DEPTS)],
                    "division": sy.s("str", "Global Shared Services"), "location": sy.s("str", "Chennai Guindy"),
                    "managerId": sy.s("str", f"SF{600000 + (i + 1) * 3}"), "payGrade": sy.s("str", "GR-11B"),
                    "standardHours": 40, "employeeClass": "Employee", "emplStatus": "Active",
                    "leaveOfAbsenceReason": sy.never(rng.choice(LEAVE_TYPES))}]}}]},
        })
    sy.data = {"d": {"results": res, "__next": sy.canary("d.__next", rng)}}
    return sy
