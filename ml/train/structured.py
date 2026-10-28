"""Purely programmatic (no LLM) generators for the structured genres. Spans come from DocBuilder, so labels are exact.

Each program is `fn(rng, lang) -> (text, spans)` and is registered in PROGRAMS under the name used by GenreSpec.program.
"""
from __future__ import annotations

import random
import re

from . import values as V
from .io import DocBuilder

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_DEPTS = ("Finance", "Engineering", "Operations", "Sales", "HR", "Procurement", "Quality", "Legal", "Support", "Admin", "Stores", "Logistics")
_DESIG = ("Associate", "Senior Executive", "Manager", "Analyst", "Team Lead", "Assistant Manager", "Engineer", "Accountant", "Supervisor")


class Emp:
    """One synthetic employee with coherent values."""

    def __init__(self, rng: random.Random, co: str, emp_style: str | None = None, corporate_mail: bool = True) -> None:
        self.rng = rng
        self.p = V.person(rng)
        self.empid = V.employee_id(rng, emp_style, co)
        self.pan = V.pan(rng, self.p.last)
        self.acct = V.bank_account(rng, fmt="plain")
        self.ifsc = V.ifsc(rng)
        self.phone = V.phone(rng)
        self.email = V.email(rng, self.p, corporate=corporate_mail)
        self.dob = V.date_of_birth(rng)
        self.aadhaar = V.aadhaar(rng)
        self.monthly = rng.randrange(12, 300) * 1000 + rng.choice((0, 0, 500, 250))
        self.dept = rng.choice(_DEPTS)
        self.desig = rng.choice(_DESIG)

    def name(self, style: str | None = None) -> str:
        return V.name_surface(self.rng, self.p, style)


def _amt(n: int, style: str, rng: random.Random) -> str:
    if style == "group":
        return V.indian_group(n)
    if style == "decimal":
        return f"{n}.00"
    if style == "western":
        return f"{n:,}.00"
    return str(n)


def emit(b: DocBuilder, pattern: str, vals: dict[str, tuple[str, str | None]]) -> None:
    """Append `pattern`, replacing {TOKEN} with vals[TOKEN] = (text, entity type or None)."""
    pos = 0
    for m in re.finditer(r"\{([A-Z_0-9]+)\}", pattern):
        b.lit(pattern[pos:m.start()])
        text, typ = vals[m.group(1)]
        b.val(text, typ)
        pos = m.end()
    b.lit(pattern[pos:])


def _table(b: DocBuilder, header: list[str], rows: list[list[tuple[str, str | None]]], right: set[int], gap: int = 2,
           ruler: str | None = "-") -> None:
    widths = [max(len(header[i]), *(len(r[i][0]) for r in rows)) for i in range(len(header))]
    b.lit((" " * gap).join(h.rjust(w) if i in right else h.ljust(w) for i, (h, w) in enumerate(zip(header, widths, strict=True))).rstrip() + "\n")
    if ruler:
        b.lit((" " * gap).join(ruler * w for w in widths) + "\n")
    for r in rows:
        for i, (cell, typ) in enumerate(r):
            pad = widths[i] - len(cell)
            if i in right:
                b.lit(" " * pad)
                b.val(cell, typ)
            else:
                b.val(cell, typ)
                b.lit(" " * pad)
            b.lit(" " * gap if i < len(r) - 1 else "\n")


def _company(rng):
    name, stem = V.company(rng)
    return name, stem, V._upper(rng, rng.choice((2, 3)))


def _month(rng):
    return f"{rng.choice(MONTHS)} {rng.randint(2022, 2026)}"


def payroll_fixed(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    style = rng.choice(("group", "plain", "decimal"))
    emps = [Emp(rng, pre) for _ in range(rng.randint(4, 12))]
    cols = ["EMP_ID", "NAME", "PAN", "BANK_AC", "IFSC", "GROSS", "NET"]
    optional = rng.sample(["DEPT", "LOC", "DOJ"], k=rng.randint(0, 2))
    chosen = [c for c in cols if c in ("EMP_ID", "NAME", "GROSS", "NET") or rng.random() < 0.8] + optional
    name_style = rng.choice(("caps", "full", "last_first"))
    b = DocBuilder()
    b.lit(f"{co.upper()} - PAYROLL REGISTER - {_month(rng).upper()}\n")
    if rng.random() < 0.5:
        b.lit(f"Run: {V.other_date(rng)}   Pay group: {rng.choice(('MONTHLY', 'WEEKLY', 'CONTRACT'))}   Page 1 of {rng.randint(1, 9)}\n")
    header, rows, tot = [], [], 0
    for e in emps:
        gross = e.monthly
        net = int(gross * rng.uniform(0.78, 0.93))
        tot += net
        cell = {"EMP_ID": (e.empid, "EMPLOYEE_ID"), "NAME": (e.name(name_style), "PERSON"), "PAN": (e.pan, "PAN"),
                "BANK_AC": (e.acct, "BANK_ACCOUNT"), "IFSC": (e.ifsc, "IFSC"), "GROSS": (_amt(gross, style, rng), "COMPENSATION"),
                "NET": (_amt(net, style, rng), "COMPENSATION"), "DEPT": (e.dept.upper(), None),
                "LOC": (V.neg_place(rng).upper(), None), "DOJ": (V.other_date(rng, "dmy_slash"), None)}
        rows.append([cell[c] for c in chosen])
    header = chosen
    right = {i for i, c in enumerate(chosen) if c in ("GROSS", "NET")}
    _table(b, header, rows, right, gap=rng.choice((1, 2, 3)), ruler=rng.choice(("-", "=", None)))
    b.lit(f"TOTAL EMPLOYEES: {len(emps)}   TOTAL NET PAYABLE: {_amt(tot, style, rng)}\n")
    return b.text, b.spans


def _delimited(rng, sep: str, quote: bool, cols: list[str], n: int, upper_header: bool = False):
    co, _, pre = _company(rng)
    b = DocBuilder()
    b.lit(sep.join(c.upper() if upper_header else c for c in cols) + "\n")
    for _ in range(n):
        e = Emp(rng, pre)
        vals = {"employee_id": (e.empid, "EMPLOYEE_ID"), "name": (e.name(rng.choice(("full", "last_first", "caps"))), "PERSON"),
                "email": (e.email, "EMAIL"), "phone": (e.phone, "PHONE"), "dob": (e.dob, "DATE_OF_BIRTH"),
                "ctc": (V.compensation(rng, style=rng.choice(("plain_group", "inr_code", "lpa", "rs_plain"))), "COMPENSATION"),
                "department": (e.dept, None), "location": (V.neg_place(rng), None), "doj": (V.other_date(rng), None),
                "pan": (e.pan, "PAN"), "bank_account": (e.acct, "BANK_ACCOUNT"), "ifsc": (e.ifsc, "IFSC"),
                "grade": (f"L{rng.randint(1, 9)}", None), "pin": (str(rng.randint(110001, 855117)), None)}
        for i, c in enumerate(cols):
            text, typ = vals[c]
            needs_q = sep in text or (quote and rng.random() < 0.3)
            if needs_q:
                b.lit('"')
                b.val(text, typ)
                b.lit('"')
            else:
                b.val(text, typ)
            b.lit(sep if i < len(cols) - 1 else "\n")
    return b.text, b.spans


def csv_export(rng: random.Random, lang: str = "en"):
    pool = ["email", "phone", "dob", "ctc", "department", "location", "doj", "grade", "pan", "pin"]
    cols = ["employee_id", "name"] + rng.sample(pool, k=rng.randint(3, 7))
    return _delimited(rng, ",", True, cols, rng.randint(3, 10), upper_header=rng.random() < 0.5)


def tsv_paste(rng: random.Random, lang: str = "en"):
    cols = rng.choice((["employee_id", "name", "bank_account", "ifsc", "pan"], ["employee_id", "name", "bank_account", "ifsc"],
                       ["name", "employee_id", "phone", "email"]))
    return _delimited(rng, "\t", False, cols, rng.randint(3, 8))


def markdown_table(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    b = DocBuilder()
    b.lit(rng.choice(("Please find the proposed revisions below.\n\n", "Revision summary for review:\n\n", "FYI the table from the HRIS:\n\n")))
    emps = [Emp(rng, pre) for _ in range(rng.randint(3, 7))]
    heads = ["Employee", "Emp ID", "Current CTC", "Revised CTC", "Hike %"]
    b.lit("| " + " | ".join(heads) + " |\n|" + "|".join("---" for _ in heads) + "|\n")
    for e in emps:
        cur = V.compensation(rng, style=rng.choice(("lpa", "lakh_L", "inr_symbol")), kind="annual")
        b.lit("| ")
        b.val(e.name(), "PERSON")
        b.lit(" | ")
        b.val(e.empid, "EMPLOYEE_ID")
        b.lit(" | ")
        b.val(cur, "COMPENSATION")
        b.lit(" | ")
        b.val(V.compensation(rng, style=rng.choice(("lpa", "lakh_L", "inr_symbol")), kind="annual"), "COMPENSATION")
        b.lit(f" | {rng.randint(3, 25)}% |\n")
    b.lit(rng.choice(("\nBudget cap for this cycle is " + V.neg_statutory(rng) + ".\n", "\nQueries: " + V.neg_role_email(rng) + "\n", "")))
    return b.text, b.spans


def salary_slip(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    e = Emp(rng, pre)
    style = rng.choice(("group", "plain", "decimal"))
    basic = int(e.monthly * 0.4)
    hra = int(basic * 0.5)
    special = e.monthly - basic - hra
    pf = int(min(basic, 15000) * 0.12)
    pt = rng.choice((200, 150, 0, 208))
    tds = int(e.monthly * rng.uniform(0, 0.08))
    gross, ded = basic + hra + special, pf + pt + tds
    net = gross - ded
    b = DocBuilder()
    b.lit(f"{co}\nPayslip for the month of {_month(rng)}\n\n")
    emit(b, "Employee Name : {N}\nEmployee ID   : {ID}\nDesignation   : " + e.desig + "\nDepartment    : " + e.dept + "\n", {"N": (e.name("full"), "PERSON"), "ID": (e.empid, "EMPLOYEE_ID")})
    b.lit("Date of Joining: " + V.other_date(rng) + "\n")
    emit(b, "PAN           : {PAN}\nUAN           : {UAN}\nBank A/C No   : {AC}\nIFSC          : {IF}\nLOP Days      : {L}\n\n",
         {"PAN": (e.pan, "PAN"), "UAN": (V.identifier(rng, "uan"), "IDENTIFIER"), "AC": (e.acct, "BANK_ACCOUNT"), "IF": (e.ifsc, "IFSC"),
          "L": (str(rng.randint(0, 3)), None)})
    b.lit("EARNINGS                         AMOUNT\n")
    for label, v in (("Basic", basic), ("HRA", hra), ("Special Allowance", special)):
        b.lit(f"{label:<28}")
        b.lit(" ")
        b.val(_amt(v, style, rng), "COMPENSATION")
        b.lit("\n")
    b.lit("Gross Earnings               ")
    b.val(_amt(gross, style, rng), "COMPENSATION")
    b.lit("\n\nDEDUCTIONS\n")
    for label, v in (("Provident Fund", pf), ("Professional Tax", pt), ("Income Tax (TDS)", tds)):
        b.lit(f"{label:<28} ")
        b.val(_amt(v, style, rng), "COMPENSATION")
        b.lit("\n")
    b.lit("Net Pay                     ")
    b.val(_amt(net, style, rng), "COMPENSATION")
    b.lit("\nNet Pay in words: Rupees ")
    b.val(V.number_words(net).title(), "COMPENSATION")
    b.lit(" Only\n\nNote: PF is computed on a wage ceiling of Rs. 15,000. This is a computer generated payslip and does not require a signature.\n")
    return b.text, b.spans


def form16_extract(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    e = Emp(rng, pre)
    gross = e.monthly * 12
    c80 = rng.choice((150000, 120000, 90000, 60000))
    b = DocBuilder()
    fy = rng.randint(2021, 2025)
    b.lit(f"FORM NO. 16\nPART A\nCertificate under section 203 of the Income-tax Act, 1961\nCertificate No. {rng.randint(10000, 99999)}\n")
    b.lit(f"Name and address of the Employer: {co}, {V.neg_place(rng)}\nTAN of the Deductor: {V._upper(rng, 4)}{V._digits(rng, 5)}{V._upper(rng, 1)}\n")
    b.lit("PAN of the Employee: ")
    b.val(e.pan, "PAN")
    b.lit("\nName and address of the Employee: ")
    b.val(e.name("full"), "PERSON")
    b.lit(f", {V.neg_namedplace(rng)}, {V.neg_place(rng)}\nAssessment Year: {fy + 1}-{(fy + 2) % 100:02d}   Period: 01-Apr-{fy} to 31-Mar-{fy + 1}\n\nPART B\n")
    b.lit("1. Gross Salary (a) Salary as per section 17(1)      ")
    b.val(V.indian_group(gross), "COMPENSATION")
    b.lit("\n2. Less: Allowances exempt under section 10 (HRA)   ")
    b.val(V.indian_group(int(gross * 0.1)), "COMPENSATION")
    b.lit("\n3. Standard deduction under section 16(ia)          75,000 (statutory)\n")
    b.lit("4. Deduction under Chapter VI-A: section 80C, limit Rs. 1,50,000, claimed ")
    b.val(V.indian_group(c80), "COMPENSATION")
    b.lit("\n5. Tax payable ")
    b.val(V.indian_group(int(gross * rng.uniform(0.02, 0.12))), "COMPENSATION")
    b.lit("\nVerified by " )
    b.val(V.name_surface(rng, V.person(rng), "full"), "PERSON")
    b.lit(f", Designation: {rng.choice(('Manager Payroll', 'Authorised Signatory', 'Head Finance'))}\n")
    return b.text, b.spans


_EMAIL_BODIES_EN = (
    "Hi {N1}, please update the bank details for {N2} (employee ID {ID2}). New account {AC}, IFSC {IF}. Effective from next payroll.",
    "Dear {N1}, I have not received my salary for the last month. My employee code is {ID2} and my registered mobile is {PH}. Kindly check.",
    "Please find the revised compensation for {N2}: {CTC} per annum. PAN on file is {PAN}.",
    "Can you confirm the date of birth of {N2} in our records? HRIS shows {DOB}. The insurer needs it for enrolment.",
)
_EMAIL_BODIES_HI = (
    "Namaste {N1} ji, mera salary abhi tak nahi aaya. Employee ID {ID2} hai, please check karke batao. Mera number {PH} hai.",
    "{N1} sir, {N2} ka bank account change karna hai. Naya account {AC} aur IFSC {IF} hai. Jaldi kar dena.",
)


def email_thread(rng: random.Random, lang: str = "en"):
    co, stem, pre = _company(rng)
    a, c, d = Emp(rng, pre), Emp(rng, pre), Emp(rng, pre)
    vals = {"N1": (a.name("first_only"), "PERSON"), "N2": (c.name("full"), "PERSON"), "ID2": (c.empid, "EMPLOYEE_ID"),
            "AC": (c.acct, "BANK_ACCOUNT"), "IF": (c.ifsc, "IFSC"), "PH": (c.phone, "PHONE"), "PAN": (c.pan, "PAN"),
            "DOB": (c.dob, "DATE_OF_BIRTH"), "CTC": (V.compensation(rng, kind="annual"), "COMPENSATION")}
    bodies = _EMAIL_BODIES_HI if lang.startswith(("hi", "en-hi")) else _EMAIL_BODIES_EN
    b = DocBuilder()
    emit(b, "From: {F} <{FE}>\nTo: {T} <{TE}>\nSubject: " + rng.choice(("RE: ", "FW: ", "")) + rng.choice(("Bank detail change", "Salary query", "Revision letter", "Records check")) + " " + V.neg_ticket(rng) + "\nDate: " + V.other_date(rng) + "\n\n",
         {"F": (d.name("full"), "PERSON"), "FE": (d.email, "EMAIL"), "T": (a.name("full"), "PERSON"), "TE": (a.email, "EMAIL")})
    emit(b, rng.choice(bodies) + "\n\nRegards,\n", vals)
    emit(b, "{S}\n" + d.desig + ", " + co + "\nM: {SP} | Ext. " + str(rng.randint(1000, 9999)) + "\n{SE}\n\n", {"S": (d.name("full"), "PERSON"), "SP": (d.phone, "PHONE"), "SE": (d.email, "EMAIL")})
    b.lit(f"On {V.other_date(rng)}, ")
    b.val(a.name("full"), "PERSON")
    b.lit(" <")
    b.val(a.email, "EMAIL")
    b.lit("> wrote:\n")
    quoted = ("> Hello, requesting you to process this at the earliest.", "> Employee ID: {ID2}", "> Contact: {PH}")
    for q in rng.sample(quoted, k=2):
        emit(b, q + "\n", vals)
    return b.text, b.spans


_CHAT_EN = (
    "hi {N2} my salary is not credited, emp id {ID2}", "my number is {PH} call me", "send it to my upi {UPI}",
    "account number is {AC} ifsc {IF}", "my mother {K} is admitted, need leave", "is this {PH} your number?",
    "aadhaar is {AAD}, pls update", "ok will check and revert", "thanks, ticket " + "{TK}" + " raised", "sure sir",
)
_CHAT_HI = (
    "bhai {N2} meri salary nahi aayi, emp id {ID2} hai", "mera number {PH} hai, call karo", "paise is UPI pe bhej do {UPI}",
    "account number {AC} hai, ifsc {IF}", "meri mummy {K} ki tabiyat kharab hai, chhutti chahiye", "kal tak ho jayega",
    "aadhaar {AAD} hai, update kar dena", "theek hai sir, dekhta hoon", "ticket {TK} bana diya hai", "arre yaar abhi tak pending hai",
    "mere papa {K} ka naam nominee me daalna hai", "ok done, shukriya",
)


def chat_log(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    a, c = Emp(rng, pre), Emp(rng, pre)
    kin = V.person(rng)
    vals = {"N2": (c.name("first_only"), "PERSON"), "ID2": (a.empid, "EMPLOYEE_ID"), "PH": (a.phone, "PHONE"),
            "UPI": (V.upi_id(rng, a.p, a.phone), "UPI_ID"), "AC": (a.acct, "BANK_ACCOUNT"), "IF": (a.ifsc, "IFSC"),
            "K": (V.name_surface(rng, kin, "first_only"), "PERSON"), "AAD": (a.aadhaar, "AADHAAR"), "TK": (V.neg_ticket(rng), None)}
    bank = _CHAT_HI if lang.startswith(("hi", "en-hi")) else _CHAT_EN
    d, m, y = rng.randint(1, 28), rng.randint(1, 12), rng.randint(22, 26)
    b = DocBuilder()
    for i in range(rng.randint(5, 14)):
        who = a if i % 2 == 0 else c
        stamp = f"{d:02d}/{m:02d}/{y}, {rng.randint(1, 12)}:{rng.randint(0, 59):02d} {rng.choice(('am', 'pm'))} - "
        b.lit(stamp)
        b.val(who.name(rng.choice(("full", "first_only"))), "PERSON")
        b.lit(": ")
        emit(b, rng.choice(bank) + "\n", vals)
    return b.text, b.spans


def _noisy(rng, b: DocBuilder, s: str, rate: float = 0.05) -> None:
    b.lit(V.ocr_noise(rng, s, rate))


def ocr_form(rng: random.Random, lang: str = "en"):
    e = Emp(rng, V._upper(rng, 2))
    b = DocBuilder()
    rate = rng.choice((0.03, 0.06, 0.1))
    _noisy(rng, b, "EMPLOYEE INFORMATION FORM\nPlease fill in BLOCK letters\n\n", rate)
    fields = [("Name", e.name("caps"), "PERSON"), ("Date of Birth", e.dob, "DATE_OF_BIRTH"), ("PAN No.", e.pan, "PAN"),
              ("Aadhaar No.", e.aadhaar, "AADHAAR"), ("Bank A/c No.", e.acct, "BANK_ACCOUNT"), ("IFSC", e.ifsc, "IFSC"),
              ("Mobile", e.phone, "PHONE"), ("Date of Joining", V.other_date(rng), None), ("City", V.neg_place(rng), None)]
    rng.shuffle(fields)
    for label, val, typ in fields:
        _noisy(rng, b, f"{label}{rng.choice((' :', ':', ' .....', ' _'))} ", rate)
        b.val(V.ocr_noise(rng, val, 0.02) if typ and rng.random() < 0.3 else val, typ)
        b.lit("\n")
    _noisy(rng, b, "\nSignature of employee ____________  Date ________\n", rate)
    return b.text, b.spans


def address_block(rng: random.Random, lang: str = "en"):
    e = Emp(rng, "X")
    b = DocBuilder()
    emit(b, "Employee: {N}\nContact: {PH}\nAddress:\n", {"N": (e.name("full"), "PERSON"), "PH": (e.phone, "PHONE")})
    b.lit(f"{rng.choice(('Flat', 'H.No.', 'Door No.'))} {rng.randint(1, 99)}/{rng.randint(1, 40)}, {rng.choice(('Sri Sai Apartments', 'Green Meadows', 'Shanti Nivas', 'Ganga Heights'))},\n")
    b.lit(V.neg_namedplace(rng) + ",\n" + V.neg_place(rng) + " - " + str(rng.randint(110001, 855117)) + "\n")
    if rng.random() < 0.5:
        emit(b, "C/o {P}\n", {"P": (V.name_surface(rng, V.person(rng), "full"), "PERSON")})
    return b.text, b.spans


def bank_change_form(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    e = Emp(rng, pre)
    old = V.bank_account(rng, fmt="plain")
    b = DocBuilder()
    b.lit(f"{co}\nREQUEST FOR CHANGE OF SALARY BANK ACCOUNT\nTicket {V.neg_ticket(rng)}\n\n")
    emit(b, "Employee name   : {N}\nEmployee ID     : {ID}\nPAN             : {PAN}\nMobile          : {PH}\nOld account no. : {OLD}\nNew account no. : {NEW}\nNew IFSC        : {IF}\n",
         {"N": (e.name("full"), "PERSON"), "ID": (e.empid, "EMPLOYEE_ID"), "PAN": (e.pan, "PAN"), "PH": (e.phone, "PHONE"),
          "OLD": (old, "BANK_ACCOUNT"), "NEW": (e.acct, "BANK_ACCOUNT"), "IF": (e.ifsc, "IFSC")})
    b.lit(f"Reason          : {rng.choice(('Bank merger', 'Salary account closed', 'Personal preference', 'Branch relocated'))}\nRequested on    : {V.other_date(rng)}\n")
    emit(b, "Approved by    : {A} (Manager)\nHR Verified by : {B}\n", {"A": (V.name_surface(rng, V.person(rng), "full"), "PERSON"), "B": (V.name_surface(rng, V.person(rng), "full"), "PERSON")})
    return b.text, b.spans


def bgv_form(rng: random.Random, lang: str = "en"):
    co, _, pre = _company(rng)
    e = Emp(rng, pre)
    b = DocBuilder()
    b.lit("BACKGROUND VERIFICATION - CANDIDATE DETAILS\n\n")
    emit(b, "Full name (as per PAN): {N}\nDate of birth: {DOB}\nPAN: {PAN}\nAadhaar: {AAD}\nPassport / DL: {ID}\nMobile: {PH}\nEmail: {EM}\n",
         {"N": (e.name("full"), "PERSON"), "DOB": (e.dob, "DATE_OF_BIRTH"), "PAN": (e.pan, "PAN"), "AAD": (e.aadhaar, "AADHAAR"),
          "ID": (V.identifier(rng, rng.choice(("passport", "dl"))), "IDENTIFIER"), "PH": (e.phone, "PHONE"), "EM": (e.email, "EMAIL")})
    b.lit(f"Current address: {rng.randint(1, 99)}, {V.neg_namedplace(rng)}, {V.neg_place(rng)} {rng.randint(110001, 855117)}\n")
    b.lit(f"Previous employer: {V.company(rng)[0]} ({V.neg_place(rng)}), tenure {rng.randint(1, 8)} years\n")
    for i in range(rng.randint(1, 2)):
        r = Emp(rng, pre)
        emit(b, f"Referee {i + 1}: {{N}}, {{PH}}, {{EM}}\n", {"N": (r.name("full"), "PERSON"), "PH": (r.phone, "PHONE"), "EM": (r.email, "EMAIL")})
    return b.text, b.spans


PROGRAMS = {
    "payroll_fixed": payroll_fixed, "csv_export": csv_export, "tsv_paste": tsv_paste, "markdown_table": markdown_table,
    "salary_slip": salary_slip, "form16_extract": form16_extract, "email_thread": email_thread, "chat_log": chat_log,
    "ocr_form": ocr_form, "address_block": address_block, "bank_change_form": bank_change_form, "bgv_form": bgv_form,
}
