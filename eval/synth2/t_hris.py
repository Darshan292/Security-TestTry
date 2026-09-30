"""hris_structured templates (v2): Workday/SuccessFactors/UKG/Oracle/Darwinbox/Keka/greytHR/SAP-shaped exports."""
from __future__ import annotations

from .core import (E, FIELDS, G, Builder, gen_aadhaar, fmt_aadhaar, gen_acct, gen_ifsc, gen_pan, gen_card, gen_phone, gen_dob, fmt_date,
                   gen_money, gen_secret, jdump, num, rand_date, style_name, tpl, DIG)

S = "hris_structured"
NS = ["wd", "hcm", "ns2", "sf", "hr", "ukg", "tns"]


def eid(p):
    return E(p.emp_id, "EMPLOYEE_ID")


def pnum(v, t):
    """bare-number entity only when the value is all digits (else quoted string)."""
    return num(v, t) if v.isdigit() else E(v, t)


def rec_person(g, p, mail=True, extra=True):
    """generic dict of person fields with this item's naming style; returns dict of key -> value/E."""
    r = g.r
    d = {g.fld("EMPLOYEE_ID"): eid(p), g.fld("PERSON"): g.nm(p, g.pick("full", "full", "lf"))}
    if mail:
        d[g.fld("EMAIL")] = g.mail(p)
    d[g.fld("PHONE")] = g.phone(p, r.choice([0, 1, 2, 5, 8]))
    return d


def xe(b, ind, tag, val):
    b(" " * ind, "<", tag, ">", val, "</", tag, ">\n")


def csv_cell(b, cell, delim=","):
    if isinstance(cell, E):
        if delim in cell.v or '"' in cell.v:
            b('"', cell, '"')
        else:
            b(cell)
    else:
        s = str(cell)
        b('"' + s.replace('"', '""') + '"' if delim in s or '"' in s else s)


def csv_row(b, cells, delim=",", eol="\n"):
    for i, c in enumerate(cells):
        if i:
            b(delim)
        csv_cell(b, c, delim)
    b(eol)


def hdr(g, types, style=None, delim=","):
    """column headers for a list of type keys/plain strings with the item's naming style"""
    out = []
    for t in types:
        out.append(g.fld(t, style) if t in ("AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC", "CARD", "UPI_ID", "EMAIL", "PHONE", "DATE_OF_BIRTH",
                                            "EMPLOYEE_ID", "COMPENSATION", "SECRET", "PERSON") else t)
    return out


def cell_for(g, t, p):
    r = g.r
    if t == "EMPLOYEE_ID":
        return eid(p)
    if t == "PERSON":
        return g.nm(p, g.pick("full", "full", "lf", "upper"))
    if t == "EMAIL":
        return g.mail(p)
    if t == "PHONE":
        return g.phone(p, g.pick(0, 1, 3, 8))
    if t == "AADHAAR":
        return E(fmt_aadhaar(r, gen_aadhaar(r), g.pick(0, 0, 1)), t)
    if t == "PAN":
        return E(gen_pan(r, p), t)
    if t == "BANK_ACCOUNT":
        return E(gen_acct(r), t)
    if t == "IFSC":
        return E(gen_ifsc(r), t)
    if t == "CARD":
        return E(gen_card(r, 0), t)
    if t == "UPI_ID":
        return E(g.v("UPI_ID", p=p), t)
    if t == "DATE_OF_BIRTH":
        return g.dob(g.pick(0, 2, 3, 6))
    if t == "COMPENSATION":
        return g.money(g.pick("annual", "monthly"), g.pick(0, 2, 8))
    if t == "SECRET":
        return g.secret("password")
    raise ValueError(t)


# ------------------------------------------------------------------ JSON
@tpl(S, "json_workday_rest_worker")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    d = {"id": "".join(r.choice("0123456789abcdef") for _ in range(32)), "descriptor": g.nm(p, "full"),
         "workerType": {"id": "Employee"},
         "person": {"legalName": {"first": g.nm(p, "first"), "last": g.nm(p, "last")},
                    "nationalIds": [{"type": {"id": "IND-Aadhaar"}, "idValue": E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR")},
                                    {"type": {"id": "IND-PAN"}, "idValue": E(gen_pan(r, p), "PAN")}],
                    "dateOfBirth": g.dob(2), "emailAddresses": [{"usageType": "WORK", "emailAddress": g.mail(p)}],
                    "phones": [{"deviceType": "Mobile", "phoneNumber": g.phone(p, 0)}]},
         "employeeID": eid(p),
         "compensation": {"annualBase": {"currency": "INR", "amount": pnum(str(r.randint(30, 4000) * 1000), "COMPENSATION")}}}
    jdump(b, d, 2)


@tpl(S, "json_sf_odata_user")
def _(g):
    b, r = g.b, g.r
    rows = []
    for _ in range(r.randint(1, 3)):
        p = g.person("roster")
        rows.append({"__metadata": {"uri": "https://api4.successfactors.com/odata/v2/User('" + p.emp_id.replace("/", "-") + "')", "type": "SFOData.User"},
                     "userId": eid(p), "displayName": g.nm(p), "email": g.mail(p), "custom01": E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"),
                     "dateOfBirth": g.dob(9), "cellPhone": g.phone(p, 1), "nationalId": E(gen_pan(r, p), "PAN")})
    jdump(b, {"d": {"results": rows}}, 2)


@tpl(S, "json_darwinbox_employee")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    st = "snake"
    d = {"status": 1, "message": "success", "employee_data": {
        "employee_no": eid(p), "full_name": g.nm(p), "company_email_id": g.mail(p), "mobile_no": g.phone(p, 1),
        "date_of_birth": g.dob(1), "pan_number": E(gen_pan(r, p), "PAN"), "aadhar_no": E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"),
        "bank_details": {"account_no": pnum(gen_acct(r), "BANK_ACCOUNT"), "ifsc_code": E(gen_ifsc(r), "IFSC")},
        "ctc": pnum(str(r.randint(30, 4000) * 1000), "COMPENSATION")}}
    jdump(b, d, 4 if r.random() < 0.5 else None)


@tpl(S, "json_keka_profile")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    d = {"succeeded": True, "data": {"employeeNumber": eid(p), "displayName": g.nm(p), "workEmail": g.mail(p),
                                     "personalDetails": {"dateOfBirth": g.dob(0), "mobilePhone": g.phone(p, 4),
                                                         "identityNo": E(fmt_aadhaar(r, gen_aadhaar(r), 1), "AADHAAR"),
                                                         "taxId": E(gen_pan(r, p), "PAN")},
                                     "salaryDetails": {"grossSalary": g.money("annual", 0), "bankAccountNumber": E(gen_acct(r), "BANK_ACCOUNT"),
                                                       "bankIFSC": E(gen_ifsc(r), "IFSC")}}}
    jdump(b, d, 2)


@tpl(S, "json_greythr_employee")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    d = {"EmployeeId": eid(p), "Name": g.nm(p, "upper"), "OfficialEmail": g.mail(p), "PersonalMobile": g.phone(p, 5),
         "DOB": g.dob(6), "AadhaarNo": E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), "PANNumber": E(gen_pan(r, p), "PAN"),
         "Salary": {"BasicPay": pnum(str(r.randint(10, 250) * 1000), "COMPENSATION"), "BankACNo": E(gen_acct(r), "BANK_ACCOUNT"), "IFSCCode": E(gen_ifsc(r), "IFSC")}}
    jdump(b, d, 2)


@tpl(S, "json_oracle_hcm_items")
def _(g):
    b, r = g.b, g.r
    items = []
    for _ in range(r.randint(2, 3)):
        p = g.person("roster")
        items.append({"PersonNumber": eid(p), "DisplayName": g.nm(p, "lf"), "NationalIdentifier": E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"),
                      "NationalIdentifierType": "IN_AADHAAR", "DateOfBirth": g.dob(2), "WorkEmail": g.mail(p), "WorkPhoneNumber": g.phone(p, 2)})
    jdump(b, {"items": items, "count": len(items), "hasMore": False}, 2)


@tpl(S, "json_payroll_run_lines")
def _(g):
    b, r = g.b, g.r
    lines = []
    for _ in range(r.randint(3, 5)):
        p = g.person("roster")
        lines.append({"emp": eid(p), "name": g.nm(p, "full"), "netPay": g.money("monthly", g.pick(0, 2)), "acct": E(gen_acct(r), "BANK_ACCOUNT"), "ifsc": E(gen_ifsc(r), "IFSC"),
                      "pan": E(gen_pan(r, p), "PAN")})
    jdump(b, {"runId": f"PR-{r.randint(2025, 2026)}-{r.randint(1, 12):02d}", "status": "APPROVED", "lines": lines}, 2)


@tpl(S, "json_dependents")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    deps = []
    for rel in r.sample(["spouse", "child", "father", "mother"], r.randint(2, 3)):
        q = g.person("external")
        deps.append({"relation": rel, "name": g.nm(q), "dob": g.dob(g.pick(1, 2, 7)), "uid": E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR")})
    jdump(b, {g.fld("EMPLOYEE_ID"): eid(p), "dependents": deps}, 2)


@tpl(S, "json_comp_history")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    hist = [{"effective": fmt_date(r, rand_date(r, 2019, 2026), 2), "reason": r.choice(["Hire", "Annual Merit", "Promotion", "Market Adj"]),
             "annualSalary": g.money("annual", r.choice([0, 2]))} for _ in range(r.randint(3, 6))]
    jdump(b, {"worker": {"empId": eid(p), "name": g.nm(p)}, "history": hist}, 2)


@tpl(S, "json_ats_candidate")
def _(g):
    b, r = g.b, g.r
    c = g.person("external")
    d = {"candidateId": f"CND-{r.randint(10000, 99999)}", "name": g.nm(c), "email": g.mail(c), "mobile": g.phone(c), "dob": g.dob(2),
         "currentCTC": g.money("annual", 4), "expectedCTC": g.money("annual", 4), "noticePeriodDays": r.choice([15, 30, 60, 90]),
         "referredBy": g.nm(g.person(), "full")}
    jdump(b, d, 2)


@tpl(S, "json_columnar_far_header")
def _(g):
    b, r = g.b, g.r
    types = ["EMPLOYEE_ID", "PERSON", "PAN", "PHONE", "BANK_ACCOUNT", "IFSC"]
    b('{\n  "meta": {"source": "hrms-export", "columns": [', ", ".join('"' + style_name(r.choice(FIELDS[t]), "camel") + '"' for t in types), "],\n")
    b('   "note": "rows are positional; see columns"},\n  "rows": [\n')
    n = r.randint(22, 30)
    for i in range(n):
        p = g.person("roster")
        cells = [eid(p), g.nm(p, "full"), E(gen_pan(r, p), "PAN"), g.phone(p, 8), E(gen_acct(r), "BANK_ACCOUNT"), E(gen_ifsc(r), "IFSC")]
        b("    [")
        for j, c in enumerate(cells):
            if j:
                b(", ")
            b('"', c, '"')
        b("]", "," if i < n - 1 else "", "\n")
    b("  ]\n}")


@tpl(S, "ndjson_hr_events")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(3, 6)):
        p = g.person("roster")
        k = r.random()
        if k < 0.34:
            d = {"evt": "bank.updated", "emp": eid(p), "acct": E(gen_acct(r), "BANK_ACCOUNT"), "ifsc": E(gen_ifsc(r), "IFSC")}
        elif k < 0.67:
            d = {"evt": "profile.viewed", "emp": eid(p), "by": g.nm(g.person("roster"), "init"), "phone": g.phone(p, 1)}
        else:
            d = {"evt": "comp.changed", "emp": eid(p), "new": g.money("annual", 0), "approver": g.mail(g.person("roster"))}
        jdump(b, d, None)
        b("\n")


# ------------------------------------------------------------------ XML
@tpl(S, "xml_workday_soap_get_workers")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    wd = "wd"
    b('<?xml version="1.0" encoding="UTF-8"?>\n<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wd="urn:com.workday/bsvc">\n <env:Body>\n  <wd:Get_Workers_Response wd:version="v41.0">\n   <wd:Response_Data>\n    <wd:Worker>\n     <wd:Worker_Data>\n')
    xe(b, 6, "wd:Worker_ID", eid(p))
    b("      <wd:Personal_Data>\n       <wd:Name_Data><wd:Legal_Name_Data><wd:Name_Detail_Data wd:Formatted_Name=\"", g.nm(p), "\"/></wd:Legal_Name_Data></wd:Name_Data>\n")
    xe(b, 7, "wd:Birth_Date", g.dob(2))
    b("       <wd:Identification_Data>\n")
    b("        <wd:National_ID><wd:National_ID_Data><wd:ID_Type_Reference><wd:ID Type=\"National_ID_Type_Code\">IND-AADHAAR</wd:ID></wd:ID_Type_Reference>\n         <wd:ID>", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), "</wd:ID></wd:National_ID_Data></wd:National_ID>\n")
    b("       </wd:Identification_Data>\n       <wd:Contact_Data>\n")
    xe(b, 8, "wd:Email_Address", g.mail(p))
    xe(b, 8, "wd:Phone_Number", g.phone(p, 2))
    b("       </wd:Contact_Data>\n      </wd:Personal_Data>\n     </wd:Worker_Data>\n    </wd:Worker>\n   </wd:Response_Data>\n  </wd:Get_Workers_Response>\n </env:Body>\n</env:Envelope>")


@tpl(S, "xml_ukg_timecard")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b('<Response Status="Success">\n <Timecard>\n  <Employee PersonNumber="', eid(p), '" FullName="', g.nm(p, "lf"), '" ', 'Mobile="', g.phone(p, 8), '"/>\n')
    for _ in range(r.randint(2, 4)):
        b('  <Punch Date="', g.date(2026, 2026, 2), '" In="', f"{r.randint(8, 10):02d}:{r.randint(0, 59):02d}", '" Out="', f"{r.randint(17, 20):02d}:{r.randint(0, 59):02d}", '"/>\n')
    b(" </Timecard>\n</Response>")


@tpl(S, "xml_sf_compound_employee")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("<queryCompoundEmployeeResponse>\n <CompoundEmployee>\n  <person>\n")
    xe(b, 3, "person_id_external", eid(p))
    b("   <personal_information>\n")
    xe(b, 4, "first_name", g.nm(p, "first"))
    xe(b, 4, "last_name", g.nm(p, "last"))
    xe(b, 4, "date_of_birth", g.dob(2))
    b("   </personal_information>\n   <national_id_card>\n")
    xe(b, 4, "national_id", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"))
    b("   </national_id_card>\n   <email_information><email_address>", g.mail(p), "</email_address></email_information>\n  </person>\n </CompoundEmployee>\n</queryCompoundEmployeeResponse>")


@tpl(S, "xml_payslip_ns")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    ns = g.pick(*NS)
    b(f'<{ns}:Payslip xmlns:{ns}="urn:kestrel:payroll:v2">\n')
    xe(b, 1, f"{ns}:EmployeeNo", eid(p))
    xe(b, 1, f"{ns}:{style_name(['emp', 'name'], 'pascal')}", g.nm(p, "full"))
    xe(b, 1, f"{ns}:PAN", E(gen_pan(r, p), "PAN"))
    xe(b, 1, f"{ns}:BankAccount", E(gen_acct(r), "BANK_ACCOUNT"))
    xe(b, 1, f"{ns}:GrossEarnings", g.money("monthly", 2))
    xe(b, 1, f"{ns}:NetPayable", g.money("monthly", 2))
    b(f"</{ns}:Payslip>")


@tpl(S, "xml_attr_roster")
def _(g):
    b, r = g.b, g.r
    b("<Employees>\n")
    for _ in range(r.randint(3, 6)):
        p = g.person("roster")
        b('  <Emp id="', eid(p), '" name="', g.nm(p, "full"), '" uidNo="', E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), '" mobile="', g.phone(p, 1), '" dob="', g.dob(2), '"/>\n')
    b("</Employees>")


@tpl(S, "xml_bank_advice_camt")
def _(g):
    b, r = g.b, g.r
    b('<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"><CstmrCdtTrfInitn>\n')
    for _ in range(r.randint(2, 4)):
        p = g.person("roster")
        b(" <CdtTrfTxInf><Amt><InstdAmt Ccy=\"INR\">", E(str(r.randint(20, 300) * 1000) + ".00", "COMPENSATION"),
          "</InstdAmt></Amt><Cdtr><Nm>", g.nm(p, "upper"), "</Nm></Cdtr><CdtrAcct><Id><Othr><Id>", E(gen_acct(r), "BANK_ACCOUNT"), "</Id></Othr></Id></CdtrAcct>",
          "<CdtrAgt><FinInstnId><ClrSysMmbId><MmbId>", E(gen_ifsc(r), "IFSC"), "</MmbId></ClrSysMmbId></FinInstnId></CdtrAgt><RmtInf><Ustrd>SALARY ", g.pick("JUN", "JUL", "AUG"), " ", eid(p), "</Ustrd></RmtInf></CdtTrfTxInf>\n")
    b("</CstmrCdtTrfInitn></Document>")


# ------------------------------------------------------------------ CSV / TSV / fixed width / kv
@tpl(S, "csv_employee_master")
def _(g):
    b, r = g.b, g.r
    types = ["EMPLOYEE_ID", "PERSON", "EMAIL", "PHONE", "DATE_OF_BIRTH", "PAN"]
    extra = r.sample(["AADHAAR", "COMPENSATION", "BANK_ACCOUNT", "IFSC"], 2)
    cols = types + extra
    csv_row(b, hdr(g, cols, g.pick("snake", "pascal", "title", "camel")))
    for _ in range(r.randint(4, 8)):
        p = g.person("roster")
        csv_row(b, [cell_for(g, t, p) for t in cols])


@tpl(S, "csv_bank_advice_semicolon")
def _(g):
    b, r = g.b, g.r
    cols = ["PERSON", "BANK_ACCOUNT", "IFSC", "COMPENSATION", "EMAIL"]
    csv_row(b, hdr(g, cols, "upper"), ";")
    for _ in range(r.randint(4, 9)):
        p = g.person()
        csv_row(b, [cell_for(g, t, p) for t in cols], ";")


@tpl(S, "csv_long_export_far_header")
def _(g):
    b, r = g.b, g.r
    cols = ["EMPLOYEE_ID", "AADHAAR", "DATE_OF_BIRTH", "UPI_ID", "PHONE"]
    csv_row(b, hdr(g, cols, g.pick("snake", "camel", "title")))
    for _ in range(r.randint(24, 32)):
        p = g.person("roster")
        csv_row(b, [cell_for(g, t, p) for t in cols])


@tpl(S, "tsv_leave_balance")
def _(g):
    b, r = g.b, g.r
    b("EmpCode\tName\tLeaveType\tBalance\tDOB\tMobile\n")
    for _ in range(r.randint(4, 8)):
        p = g.person("roster")
        b(eid(p), "\t", g.nm(p, "lfu"), "\t", g.pick("PL", "CL", "SL"), "\t", f"{r.randint(0, 24)}.{r.choice(['0', '5'])}", "\t", g.dob(1), "\t", g.phone(p, 8), "\n")


@tpl(S, "tsv_cards_register")
def _(g):
    b, r = g.b, g.r
    b("holder\tcard_no\tissued\temail\n")
    for _ in range(r.randint(3, 7)):
        p = g.person()
        b(g.nm(p, "full"), "\t", E(gen_card(r, g.pick(0, 1, 2)), "CARD"), "\t", g.date(2022, 2026, 2), "\t", g.mail(p), "\n")


@tpl(S, "fixed_width_neft_file")
def _(g):
    b, r = g.b, g.r
    b("H" + "SALARY CREDIT".ljust(20) + "KESTREL SYSTEMS".ljust(20) + rand_date(r, 2025, 2026).strftime("%Y%m%d") + "\n")
    for i in range(r.randint(4, 8)):
        p = g.person()
        b("D", f"{i + 1:04d}", " ", E(gen_acct(r), "BANK_ACCOUNT"))
        pad = 20 - 0
        b(" " * 3)
        b(E(gen_ifsc(r), "IFSC"), " ")
        nm = g.nm(p, "upper")
        b(nm, " " * max(1, 34 - len(nm.v)))
        b(E(f"{r.randint(15, 350) * 1000}.00", "COMPENSATION"), "\n")
    b("T", f"{i + 1:04d}\n")


@tpl(S, "kv_pipe_export")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(2, 4)):
        p = g.person("roster")
        parts = [(g.fld("EMPLOYEE_ID", "upper"), eid(p)), (g.fld("PERSON", "upper"), g.nm(p, "full")), (g.fld("AADHAAR", "upper"), E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR")),
                 (g.fld("DATE_OF_BIRTH", "upper"), g.dob(2)), (g.fld("COMPENSATION", "upper"), g.money("annual", 2))]
        for i, (k, v) in enumerate(parts):
            b(k, "=", v, "|" if i < len(parts) - 1 else "\n")


@tpl(S, "sap_infotype_dump")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("*** PA0002 ***\nPERNR ", eid(p), "\nNACHN ", g.nm(p, "last"), "\nVORNA ", g.nm(p, "first"), "\nGBDAT ", g.dob(9), "\n")
    b("*** PA0008 ***\nTRFAR 01  ANSAL ", g.money("annual", 2), " WAERS INR\n")
    b("*** PA0009 ***\nBANKL ", E(gen_ifsc(r), "IFSC"), "  BANKN ", E(gen_acct(r), "BANK_ACCOUNT"), "  ZLSCH T\n*** PA0185 ***\nICNUM ", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), "  ICTYP 05\n")


@tpl(S, "ini_employee_sections")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("[identity]\n", g.fld("PERSON", "snake"), " = ", g.nm(p), "\n", g.fld("EMPLOYEE_ID", "snake"), " = ", eid(p), "\n", g.fld("AADHAAR", "snake"), " = ", E(fmt_aadhaar(r, gen_aadhaar(r), 1), "AADHAAR"), "\n\n[contact]\n")
    b("mail = ", g.mail(p), "\nmobile = ", g.phone(p, 9), "\n\n[payroll]\nctc = ", g.money("annual", 4), "\nbank_ac = ", E(gen_acct(r), "BANK_ACCOUNT"), "\n")


@tpl(S, "vcard_contact_export")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(2, 3)):
        p = g.person()
        b("BEGIN:VCARD\nVERSION:3.0\nFN:", g.nm(p), "\nN:", g.nm(p, "last"), ";", g.nm(p, "first"), ";;;\nEMAIL;TYPE=WORK:", g.mail(p), "\nTEL;TYPE=CELL:", g.phone(p, 1), "\nBDAY:", g.dob(2), "\nEND:VCARD\n")


@tpl(S, "ldif_directory_entry")
def _(g):
    b, r = g.b, g.r
    p = g.person("roster")
    b("dn: uid=", g.nm(p, "first"), ",ou=people,dc=kestrelsys,dc=in\nobjectClass: inetOrgPerson\ncn: ", g.nm(p), "\nsn: ", g.nm(p, "last"), "\nmail: ", g.mail(p),
      "\ntelephoneNumber: ", g.phone(p, 0), "\nemployeeNumber: ", eid(p), "\nuserPassword:: ", g.secret("b64"), "\n")


@tpl(S, "yaml_employee_records")
def _(g):
    b, r = g.b, g.r
    b("employees:\n")
    for _ in range(r.randint(2, 3)):
        p = g.person("roster")
        b("  - id: ", eid(p), "\n    name: ", g.nm(p, "full"), "\n    contact: {email: ", g.mail(p), ", phone: '", g.phone(p, 1), "'}\n    nat_id: '", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), "'\n    dob: ", g.dob(2), "\n")


@tpl(S, "markdown_table_paste")
def _(g):
    b, r = g.b, g.r
    cols = ["EMPLOYEE_ID", "PERSON", "PAN", "COMPENSATION"]
    b("| ", " | ".join(hdr(g, cols, "title")), " |\n|---|---|---|---|\n")
    for _ in range(r.randint(3, 6)):
        p = g.person("roster")
        b("| ", eid(p), " | ", g.nm(p, "full"), " | ", E(gen_pan(r, p), "PAN"), " | ", g.money("annual", g.pick(0, 2)), " |\n")


@tpl(S, "positional_flatfile_records")
def _(g):
    b, r = g.b, g.r
    b("HDR|HRMS|1.4\n")
    for _ in range(r.randint(3, 6)):
        p = g.person("roster")
        b("EMP|", eid(p), "|", g.nm(p, "lf"), "|", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"), "|", g.phone(p, 8), "|", g.dob(9), "|", g.mail(p), "\n")
    b("TRL|done")


@tpl(S, "json_upi_payout_batch")
def _(g):
    b, r = g.b, g.r
    rows = []
    for _ in range(r.randint(3, 5)):
        p = g.person("external") if r.random() < 0.4 else g.person("roster")
        rows.append({"payee": g.nm(p), "vpa": E(g.v("UPI_ID", p=p), "UPI_ID"), "amt": g.money("bonus", 2), "note": g.pick("diwali gift", "referral bonus", "spot award")})
    jdump(b, {"batch": f"UPI-{r.randint(1000, 9999)}", "payouts": rows}, 2)


@tpl(S, "csv_reimbursement_upi")
def _(g):
    b, r = g.b, g.r
    cols = ["EMPLOYEE_ID", "PERSON", "UPI_ID", "PHONE"]
    csv_row(b, hdr(g, cols, g.pick("snake", "title", "camel")))
    for _ in range(r.randint(4, 8)):
        p = g.person("roster")
        csv_row(b, [cell_for(g, t, p) for t in cols])


@tpl(S, "json_card_vault_records")
def _(g):
    b, r = g.b, g.r
    recs = []
    for _ in range(r.randint(2, 4)):
        p = g.person("roster")
        recs.append({"owner": eid(p), "pan": E(gen_card(r, g.pick(0, 1)), "CARD"), "expiry": f"{r.randint(1, 12):02d}/{r.randint(27, 32)}", "network": g.pick("VISA", "MASTERCARD", "RUPAY")})
    jdump(b, {g.fld("CARD", "snake") + "_vault": recs}, 2)


@tpl(S, "json_integration_settings")
def _(g):
    b, r = g.b, g.r
    jdump(b, {"connectors": [{"name": "bank-gateway", "client_id": "kestrel-hr", g.fld("SECRET"): g.secret("opaque")},
                             {"name": "attendance", "auth": {"token": g.secret("jwt")}},
                             {"name": "sms", "api_key": g.secret("hex32"), "sender": "KESTRL"}], "owner": g.mail(g.person("roster"))}, 2)


@tpl(S, "csv_vendor_payee_master")
def _(g):
    b, r = g.b, g.r
    cols = ["PERSON", "PAN", "BANK_ACCOUNT", "IFSC", "PHONE", "EMAIL"]
    csv_row(b, hdr(g, cols, g.pick("snake", "pascal", "title")))
    for _ in range(r.randint(5, 10)):
        p = g.person("external")
        csv_row(b, [cell_for(g, t, p) if t != "EMAIL" else E(p.email, "EMAIL") for t in cols])


@tpl(S, "csv_candidate_pipeline")
def _(g):
    b, r = g.b, g.r
    csv_row(b, ["Candidate", "Mobile", "Email", "DOB", "Current CTC", "Expected CTC", "Stage"], "\t" if r.random() < 0.3 else ",")
    d = "," 
    for _ in range(r.randint(5, 10)):
        p = g.person("external")
        csv_row(b, [g.nm(p, g.pick("full", "lf")), g.phone(p, g.pick(0, 3, 8)), g.mail(p), g.dob(g.pick(0, 2, 3)), g.money("annual", g.pick(0, 2, 4)), g.money("annual", g.pick(0, 2, 4)), g.pick("Screen", "L1", "L2", "Offer", "Joined")])


@tpl(S, "xml_ats_candidate_feed")
def _(g):
    b, r = g.b, g.r
    b("<CandidateFeed>\n")
    for _ in range(r.randint(3, 6)):
        p = g.person("external")
        b(" <Candidate>\n")
        xe(b, 2, "FullName", g.nm(p))
        xe(b, 2, "PrimaryEmail", g.mail(p))
        xe(b, 2, "MobileNo", g.phone(p, 1))
        xe(b, 2, "BirthDate", g.dob(2))
        xe(b, 2, "ExpectedSalary", g.money("annual", 4))
        b(" </Candidate>\n")
    b("</CandidateFeed>")


@tpl(S, "json_referral_ledger")
def _(g):
    b, r = g.b, g.r
    rows = []
    for _ in range(r.randint(3, 6)):
        ref, cand = g.person(), g.person("external")
        rows.append({"referrer": g.nm(ref, "full"), "candidate": g.nm(cand, "full"), "candidateContact": g.phone(cand, 1), "bonusEligible": g.money("bonus", 0)})
    jdump(b, {"referrals": rows}, 2)
