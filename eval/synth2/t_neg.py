"""negatives (v2): text dense in look-alikes with NO gold spans."""
from __future__ import annotations

from .core import (gen_aadhaar, gen_card, luhn_check_digit, tpl, verhoeff_check_digit, verhoeff_valid, inr_group, west_group, rand_date, fmt_date,
                   gen_secret, UP, DIG)

S = "negatives"
PUBLIC = ["Infosys", "Tata Consultancy Services", "HDFC Bank", "Reliance Industries", "Wipro", "Larsen & Toubro", "Bharti Airtel", "Asian Paints",
          "Hindustan Unilever", "Maruti Suzuki", "Sun Pharma", "Titan Company", "Mahindra & Mahindra", "ITC Limited", "UltraTech Cement"]
TITLES = ["Chief Financial Officer", "Head of Human Resources", "Managing Director", "Chief Technology Officer", "Company Secretary", "Vice President Operations",
          "General Manager Finance", "Chairman of the Board", "Independent Director", "Internal Complaints Committee", "Head of Talent Acquisition"]
POLICIES = ["Leave Encashment Policy", "Code of Business Conduct", "Whistleblower Policy", "Anti Bribery and Corruption Policy", "Work From Home Guidelines",
            "Prevention of Sexual Harassment Policy", "Employee Stock Option Plan", "Travel and Expense Policy", "Information Security Standard", "Gratuity Rules"]


def n12(g):
    r = g.r
    if r.random() < 0.10:
        p = r.choice("23456789") + "".join(r.choice(DIG) for _ in range(10))
        return p + verhoeff_check_digit(p)
    while True:
        v = r.choice("123456789") + "".join(r.choice(DIG) for _ in range(11))
        if not verhoeff_valid(v):
            return v


def gstin(g):
    r = g.r
    return f"{r.randint(1, 37):02d}" + "".join(r.choice(UP) for _ in range(5)) + "".join(r.choice(DIG) for _ in range(4)) + r.choice(UP) + r.choice("123456789") + "Z" + r.choice(DIG + UP)


def hexs(g, n):
    return "".join(g.r.choice("0123456789abcdef") for _ in range(n))


def uuid(g):
    return "-".join(hexs(g, n) for n in (8, 4, 4, 4, 12))


def inr(g, lo=1000, hi=9000000, dec=True):
    n = g.r.randint(lo, hi)
    return "₹" + inr_group(n) + (f".{g.r.randint(0, 99):02d}" if dec else "")


def imei(g):
    p = "".join(g.r.choice(DIG) for _ in range(14))
    return p + luhn_check_digit(p)


@tpl(S, "invoice_register")
def _(g):
    b, r = g.b, g.r
    c = r.choice(PUBLIC)
    b("Vendor invoices - ", g.pick("AP aging", "month-end accrual"), " (", c, ")\nInvoice No | GSTIN | Date | Amount\n")
    for _ in range(r.randint(5, 9)):
        b(n12(g), " | ", gstin(g), " | ", fmt_date(r, rand_date(r, 2025, 2026), 2), " | ", inr(g), "\n")


@tpl(S, "purchase_orders")
def _(g):
    b, r = g.b, g.r
    b("PO summary - Procurement\n")
    for _ in range(r.randint(4, 8)):
        b("PO ", g.pick("45", "46", "47"), "".join(r.choice(DIG) for _ in range(8)), "  item ", g.pick("LAP", "MON", "DSK", "CHR"), "-", str(r.randint(1000, 9999)), "  qty ", str(r.randint(1, 200)), "  rate ", inr(g, 500, 90000), "  ref ", n12(g), "\n")


@tpl(S, "order_tracking")
def _(g):
    b, r = g.b, g.r
    b("Your order has shipped!\nOrder No: ", f"{r.randint(400, 408)}-{r.randint(1000000, 9999999)}-{r.randint(1000000, 9999999)}", "\nAWB: ", n12(g), " (", g.pick("Delhivery", "BlueDart", "Ecom Express"), ")\nTracking id ",
      "".join(r.choice(UP + DIG) for _ in range(14)), "\nShipment ref ", str(r.randint(7, 9)), "".join(r.choice(DIG) for _ in range(9)), "\nExpected by ", fmt_date(r, rand_date(r, 2026, 2026), 4), ". Order total ", inr(g, 199, 90000), "\n")


@tpl(S, "device_inventory")
def _(g):
    b, r = g.b, g.r
    b("Asset register - IT\n")
    for _ in range(r.randint(4, 7)):
        mac = ":".join(hexs(g, 2) for _ in range(6))
        b("AST-", f"{r.randint(1, 99999):07d}", "  IMEI ", imei(g), "  SN ", "".join(r.choice(UP + DIG) for _ in range(10)), "  MAC ", mac, "\n")


@tpl(S, "hash_uuid_table")
def _(g):
    b, r = g.b, g.r
    b("Artifact manifest\n")
    for _ in range(r.randint(4, 7)):
        b(g.pick("sha256", "md5", "sha1", "etag", "commit"), ": ", hexs(g, g.pick(*[32, 40, 64])), "   id=", uuid(g), "\n")


@tpl(S, "network_inventory")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(5, 9)):
        b(f"{r.choice(['10', '172', '192'])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}/{r.choice([16, 24, 28, 32])}  vlan {r.randint(10, 990)}  gw {r.randint(1, 254)}  fw v{r.randint(1, 9)}.{r.randint(0, 20)}.{r.randint(0, 99)}\n")


@tpl(S, "release_notes")
def _(g):
    b, r = g.b, g.r
    b("## Release ", str(r.randint(1, 5)), ".", str(r.randint(0, 30)), ".", str(r.randint(0, 20)), " (", fmt_date(r, rand_date(r, 2025, 2026), 2), ")\n")
    for _ in range(r.randint(4, 8)):
        b("- ", g.pick("PAY", "HRD", "ATS", "PLAT", "BEN"), "-", str(r.randint(100, 9999)), ": ", g.pick("fix rounding in arrears", "upgrade lib to v", "add new leave type", "migrate cron to k8s", "reduce p95 latency to "), str(r.randint(1, 900)), g.pick("", " ms", "%"), "\n")


@tpl(S, "market_watch")
def _(g):
    b, r = g.b, g.r
    b("Market wrap - ", fmt_date(r, rand_date(r, 2025, 2026), 4), "\n")
    for c in r.sample(PUBLIC, r.randint(4, 6)):
        b(c, " closed at ", inr(g, 300, 9000), " (", g.pick("+", "-"), f"{r.uniform(0.1, 4.9):.2f}%), market cap ", inr(g, 1000, 900000, False), " crore, 52-week high ", inr(g, 500, 9500), "\n")


@tpl(S, "policy_titles_text")
def _(g):
    b, r = g.b, g.r
    b("Section ", str(r.randint(1, 9)), ". Approvals\nAs per the ", g.pick(*POLICIES), ", requests above ", inr(g, 50000, 900000, False), " require sign-off from the ", g.pick(*TITLES), " and the ", g.pick(*TITLES),
      ". Effective from ", fmt_date(r, rand_date(r, 2024, 2026), 4), ", the ", g.pick(*POLICIES), " supersedes circular no. ", str(r.randint(10, 99)), "/", str(r.randint(2019, 2025)), ". Employer PF contribution is 12% of basic wages; ESI applies below ",
      inr(g, 20000, 22000, False), " gross wages per month.\n")


@tpl(S, "ticket_export")
def _(g):
    b, r = g.b, g.r
    b("Key,Summary,Status,Created,Updated\n")
    for _ in range(r.randint(5, 9)):
        k = g.pick("PAY", "HRD", "IT", "BEN", "RCT")
        b(f"{k}-{r.randint(100, 9999)},{g.pick('Payslip PDF blank', 'Leave balance mismatch', 'VPN token expired', 'Onboarding checklist stuck', 'Bulk upload fails')},{g.pick('Open', 'In Review', 'Done')},{fmt_date(r, rand_date(r, 2026, 2026), 2)},{fmt_date(r, rand_date(r, 2026, 2026), 2)}\n")


@tpl(S, "holiday_calendar_dates")
def _(g):
    b, r = g.b, g.r
    b("Company holiday list ", str(r.randint(2025, 2026)), "\n")
    for n in ["Republic Day", "Holi", "Independence Day", "Dussehra", "Diwali", "Christmas"]:
        b(n, " - ", fmt_date(r, rand_date(r, 2025, 2026), g.pick(0, 3, 4, 6, 8)), "\n")
    b("Appraisal window closes ", fmt_date(r, rand_date(r, 2026, 2026), 1), "; payroll cutoff ", fmt_date(r, rand_date(r, 2026, 2026), 6), ".\n")


@tpl(S, "budget_table")
def _(g):
    b, r = g.b, g.r
    b("FY", str(r.randint(25, 27)), " budget vs actuals (₹ crore)\n")
    for h in ["Cloud infra", "Recruitment ads", "Facilities", "Training", "Travel", "Software licences"]:
        b(f"{h:<18}{r.uniform(0.2, 90):>8.2f}{r.uniform(0.2, 90):>8.2f}   variance {r.uniform(-9, 9):+.1f}%\n")
    b("Capex approval ceiling: ", inr(g, 1000000, 90000000, False), "\n")


@tpl(S, "product_catalog")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(4, 8)):
        b("SKU ", "".join(r.choice(UP) for _ in range(3)), "-", str(r.randint(1000, 99999)), "  EAN ", "89", "".join(r.choice(DIG) for _ in range(11)), "  HSN ", str(r.randint(1000, 9999)), "  MRP ", inr(g, 99, 49999), "\n")


@tpl(S, "bank_txn_lookalikes")
def _(g):
    b, r = g.b, g.r
    b("Recon - vendor settlements\n")
    for _ in range(r.randint(4, 7)):
        b("UTR ", g.pick("HDFCR", "SBIN", "ICICN"), "".join(r.choice(DIG) for _ in range(11)), "  RRN ", n12(g), "  cheque ", "".join(r.choice(DIG) for _ in range(6)), "  amount ", inr(g), "\n")


@tpl(S, "meeting_minutes_public")
def _(g):
    b, r = g.b, g.r
    b("Steering committee minutes\nAttendees: ", g.pick(*TITLES), ", ", g.pick(*TITLES), ", ", g.pick(*TITLES), ".\nDiscussed the ", g.pick(*POLICIES), " rollout across ", g.pick("Pune", "Chennai", "Gurugram", "Kolkata"),
      " and the vendor proposal from ", r.choice(PUBLIC), " for ", inr(g, 500000, 90000000, False), ". Next review on ", fmt_date(r, rand_date(r, 2026, 2026), 4), ".\n")


@tpl(S, "card_like_orders")
def _(g):
    b, r = g.b, g.r
    for _ in range(r.randint(3, 6)):
        while True:
            v = "".join(r.choice(DIG) for _ in range(16))
            if v[0] in "4567" and luhn_check_digit(v[:-1]) != v[-1]:
                break
        b("Txn ref ", v, "  gateway id ", uuid(g), "  amount ", inr(g, 100, 99999), "\n")


@tpl(S, "isin_cin_codes")
def _(g):
    b, r = g.b, g.r
    for c in r.sample(PUBLIC, r.randint(3, 5)):
        b(c, "  ISIN INE", f"{r.randint(1, 999):03d}", r.choice("ABCDEFGHK"), f"{r.randint(1, 20):02d}", str(r.randint(10, 29)), str(r.randint(0, 9)), "  CIN ", r.choice("LU"), str(r.randint(10000, 99999)), r.choice(["MH", "KA", "DL", "TN"]), str(r.randint(1980, 2015)), "PLC", f"{r.randint(1, 999999):06d}", "\n")


@tpl(S, "code_placeholder_secrets")
def _(g):
    b, r = g.b, g.r
    b("# config template - fill from vault\nAPI_KEY = os.environ[\"PAYROLL_API_KEY\"]\nDB_PASSWORD = \"<REDACTED>\"\nSECRET_KEY = \"changeme\"\nTOKEN = \"your-token-here\"\nAADHAAR_RE = r\"^[2-9][0-9]{3}\\s?[0-9]{4}\\s?[0-9]{4}$\"\nPAN_RE = r\"[A-Z]{5}[0-9]{4}[A-Z]\"\nVERSION = \"",
      str(r.randint(1, 4)), ".", str(r.randint(0, 20)), ".", str(r.randint(0, 9)), "\"\nBUILD = ", str(r.randint(1000, 99999)), "\nCHECKSUM_SHA256 = \"", hexs(g, 64), "\"\n")


@tpl(S, "hr_stats_report")
def _(g):
    b, r = g.b, g.r
    b("People metrics Q", str(r.randint(1, 4)), "\nHeadcount ", str(r.randint(800, 9000)), " | Attrition ", f"{r.uniform(6, 24):.1f}", "% | Offer acceptance ", str(r.randint(60, 95)), "% | Time to hire ", str(r.randint(20, 70)),
      " days\nTraining spend ", inr(g, 100000, 9000000, False), " across ", str(r.randint(100, 900)), " sessions. Reference: ", g.pick(*POLICIES), ".\n")


@tpl(S, "vendor_onboarding_ids")
def _(g):
    b, r = g.b, g.r
    c, dom = g.corp()
    b("Vendor master - ", c, "\nVendor code V", f"{r.randint(1, 99999):06d}", "\nGSTIN ", gstin(g), "\nCIN U", str(r.randint(10000, 99999)), r.choice(["MH", "KA", "DL"]), str(r.randint(1990, 2020)), "PTC", f"{r.randint(1, 999999):06d}",
      "\nMSME reg UDYAM-", r.choice(["MH", "KA", "TN"]), "-", f"{r.randint(0, 99):02d}", "-", f"{r.randint(0, 9999999):07d}", "\nWebsite www.", dom, "\nPayment terms net ", str(r.choice([15, 30, 45, 60])), " days; credit limit ", inr(g, 100000, 9000000, False), "\n")
