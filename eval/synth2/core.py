"""Core machinery for the Release-2 corpus (v2): name pools, roster, value generators, builder context.

Helpers (Builder/E span tracking, Verhoeff/Luhn, INR grouping, JSON serialiser) are reused from the R1
generator; every template, field-name pool, phrase and value shape in eval/synth2 is new.
"""
from __future__ import annotations

import random
import re
import string
from datetime import date, timedelta

from eval.synth.generate import (Builder, E, NAMES, build_name_tables, inr_group, jdump,  # noqa: F401
                                 json_esc, luhn_check_digit, luhn_valid, num, verhoeff_check_digit,
                                 verhoeff_valid, west_group)

GENERATOR_VERSION = "2.0.0"
DEFAULT_SEED = 20260930
SPLITS = ("dev2", "test2")
SLICES = ("hr_free_text", "hris_structured", "integration_debug", "negatives")
TYPES = ("PERSON", "EMAIL", "PHONE", "AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC", "CARD", "UPI_ID",
         "SECRET", "EMPLOYEE_ID", "COMPENSATION", "DATE_OF_BIRTH")
MIN_POS = {t: 150 for t in TYPES}
MIN_POS.update({"PERSON": 400, "COMPENSATION": 200})
MIN_SLICE = {"hr_free_text": 600, "hris_structured": 400, "integration_debug": 250, "negatives": 400}
TENANT_DOMAIN = "kestrelsys.in"

# --------------------------------------------------------------------------- names
EXTRA = {
    "north": {"M": "Abhay Anshul Bhanu Dilip Eklavya Hridayesh Jagdish Kamlesh Laxman Nishant Pushkar Raghuvir Shailendra Tejendra Vedant Brijmohan Omprakash",
              "F": "Aarohi Bhumika Chandni Damini Falguni Hemlata Indu Kanchan Laxmi Mridula Nalini Pragya Rachna Sunaina Tripti Vartika",
              "L": "Agnihotri Bahuguna Chaudhary Dhawan Goswami Handa Jaiswal Kanwar Lohia Mangal Nijhawan Paliwal Rana Saini Tripathi Vohra"},
    "south": {"M": "Aravind Bhaskaran Cheran Dayanand Eshwar Govindan Hanumanth Jeevan Karunakaran Loganathan Mugilan Nallasamy Prabhakaran Rameshwar Sankar Thirumal Vetri",
              "F": "Abirami Bhavani Chandrika Dhivya Eswari Gowri Hemalatha Jothi Kokila Mythili Nivedha Oviya Poornima Rajalakshmi Sangeetha Thamarai Yashoda",
              "L": "Aiyer Bhat Chidambaram Devarajan Gopalakrishnan Hariharan Jagannathan Kalaimani Mahalingam Nedungadi Padmanabhan Rangarajan Sivaramakrishnan Thyagarajan Kumaraswamy"},
    "east": {"M": "Ambarish Bhaskar Debabrata Gopal Kamalesh Lakshman Mrigen Nitai Onkar Probir Ratan Shibaji Tarun",
             "F": "Aloka Bipasha Chhaya Durga Gargi Indrani Jayashree Keya Lopamudra Manjari Nabanita Oindrila Ritika Sohini",
             "L": "Adhikari Bagchi Chanda Dutta Goswami Hazra Kundu Laha Maiti Nag Paul Rakshit Sinha Talukdar"},
    "west": {"M": "Advait Bhargav Chaitanya Devang Gunvant Hardik Ishan Jaydeep Kunj Lalit Meet Nakul Om Pranav Raunak Smit",
             "F": "Aarushi Bhoomi Chaitali Disha Esha Foram Gargi Hiral Ishita Jinal Kruti Lopa Mital Nidhi Ojal",
             "L": "Amin Bhatt Chokshi Dave Gadhvi Hirani Joshipura Kansara Lakhani Nanda Oza Panchal Rupani Trivedi Vaidya Walke"},
    "northeast": {"M": "Achyut Bidyut Chandan Dhanjit Eklabya Gyanendra Hiranya Indrajit Jugal Khagen",
                  "F": "Anjali Bimala Chandrama Daisy Ereni Fanai Gitanjali Hasina Imnayanla Jemima",
                  "L": "Ao Bezbaruah Chutia Deori Ezung Gohain Hrangkhawl Jamir Khiamniungan Lalrinawma"},
    "muslim": {"M": "Abdullah Burhan Daud Ejaz Fahim Ghulam Habib Ilyas Jabir Kamran Latif Maqbool Nasir Obaid Pervez Rafiq Saleem",
               "F": "Aaliya Bano Dania Erum Firdous Gulshan Husna Inaya Jannat Khushnuma Laila Mumtaz Nargis Rahat Shabnam",
               "L": "Amanullah Bhat Chaudhry Deshmukh Fakhri Gilani Hakim Jilani Khatib Lakhani Makrani Nadvi Peerzada Quraishi Rehmani Tak"},
    "christian": {"M": "Alban Basil Clement Dennis Ebenezer Felix Godwin Herbert Ignatius Jeremiah Kenneth Lionel Marcus Nathan",
                  "F": "Adeline Beulah Concetta Doreen Elsie Flora Grace Hilda Ivy Judith Kezia Lydia Mabel Naomi",
                  "L": "Abreo Baptista Carvalho Dsilva Fonseca Gracias Henriques Isaac Joseph Kurian Lewis Mathias Nazareth Oliveira Peixoto"},
    "sikh": {"M": "Amolak Bhupinder Charnjit Dilbagh Gurbux Harbhajan Ishar Jarnail Kanwarjit Lovepreet Mohinder Nirmal",
             "F": "Amarpreet Baljinder Charanpreet Dilpreet Gurkiran Harman Inderjeet Jasbir Kiranpal Lakhbir",
             "L": "Ahuja Badwal Chadha Duggal Ghuman Hundal Jaggi Khehra Lakhanpal Matharu Nagpal Pannu"},
    "parsi": {"M": "Adi Bomi Cawas Dinshaw Eruch Framroze Gustad Hormasji Jal Khushroo Marzban",
              "F": "Anahita Banoo Coomi Dhun Freni Gool Homai Jer Khurshid Mani",
              "L": "Anklesaria Bamboat Cursetji Damania Engineer Fitter Gagrat Hansotia Jassawalla Katrak Lentin Mehta"},
    "global": {"M": "Alejandro Bogdan Callum Dieter Eero Florian Gustavo Ilya Jakub Konstantin Lorenzo Magnus Niklas Oskar Pavel Rafael Tobias Valentin Chidi Kwame Emeka Tariq Bjorn",
               "F": "Annika Beatrix Camila Daria Elsa Freya Greta Helga Ilona Johanna Klara Leonie Marit Noemi Paloma Renata Saskia Tereza Vera Ngozi Amara Yasmin Zeynep",
               "L": "Aaltonen Bergstrom Castellano Dragomir Eriksen Fontaine Gallagher Hartmann Iglesias Jorgensen Karlsson Lindqvist Moretti Nakagawa Pellegrini Quintero Rasmussen Sokolov Tkachenko Vasquez Yilmaz Zielinski Okafor Adeyemi Mensah Abernathy Kovalenko Lindgren Brennan O'Connor Fitzgerald"},
}
REGION_WEIGHTS = {"north": 20, "south": 19, "east": 10, "west": 13, "northeast": 4, "muslim": 9,
                  "christian": 7, "sikh": 5, "parsi": 2, "global": 11}
NER_REGION_WEIGHTS = {"north": 14, "south": 14, "east": 8, "west": 9, "northeast": 5, "muslim": 9,
                      "christian": 8, "sikh": 5, "parsi": 3, "global": 25}
# tokens that are also ordinary words in this corpus -> never used as person names
BLOCK = {"doctor", "engineer", "contractor", "marker", "cooper", "porter", "hunter", "carpenter", "weaver",
         "baker", "miller", "knight", "park", "ward", "ray", "pal", "kar", "mann", "dar", "mir", "lal",
         "sen", "das", "kim", "wang", "cox", "hicks", "bell", "gill", "bose", "lone", "toor", "kang",
         "puri", "bedi", "wells", "webb", "hall", "mody", "sethi", "wani", "fitter", "sind", "saha",
         "nag", "paul", "dave", "amin", "ao", "hakim", "lewis", "joseph", "isaac", "jal", "mani", "coomi",
         "joy", "jun", "suzuki", "larsen", "ganesh", "grace", "hilda", "ivy", "naomi", "flora", "mabel", "june", "victoria", "charity"}
REGIONS = sorted(NAMES)
GROUPS = ("roster_dev2", "roster_test2", "ext_dev2", "ext_test2", "ner")
FRACTIONS = (0.30, 0.30, 0.12, 0.12, 0.16)


def _alpha_tokens(w):
    return {m.lower() for m in re.findall(r"[^\W\d_]+", w) if len(m) > 1}


def build_tables():
    """first[(region,gender)] / last[region] -> sorted lists; no alphabetic token appears twice anywhere."""
    seen, first, last = set(), {}, {}

    def take(w):
        toks = _alpha_tokens(w)
        if not toks or toks & seen or toks & BLOCK or len(w) < 3:
            return False
        seen.update(toks)
        return True
    for region in REGIONS:
        for g in "MF":
            words = (NAMES[region][g] + " " + EXTRA[region].get(g, "")).split()
            first[(region, g)] = sorted(w for w in words if take(w))
    for region in REGIONS:
        words = (NAMES[region]["L"] + " " + EXTRA[region].get("L", "")).split()
        last[region] = sorted(w for w in words if take(w))
    return first, last


def _split_list(lst, r):
    l = list(lst)
    r.shuffle(l)
    n = len(l)
    cuts, acc, prev = [], 0.0, 0
    parts = []
    for i, f in enumerate(FRACTIONS):
        acc += f
        c = n if i == len(FRACTIONS) - 1 else max(prev + 1, round(n * acc))
        parts.append(l[prev:c])
        prev = c
    assert all(parts), "name list too small to split"
    return dict(zip(GROUPS, parts))


def build_pools(seed):
    r = random.Random(f"{seed}|names2")
    first, last = build_tables()
    pools = {g: {"first": {}, "last": {}} for g in GROUPS}
    for key in sorted(first):
        for g, part in _split_list(first[key], r).items():
            pools[g]["first"][key] = part
    for key in sorted(last):
        for g, part in _split_list(last[key], r).items():
            pools[g]["last"][key] = part
    return pools


def draw_name(r, pool, weights=None):
    weights = weights or REGION_WEIGHTS
    regions = sorted(weights)
    region = r.choices(regions, [weights[x] for x in regions])[0]
    gdr = r.choice("MF")
    return r.choice(pool["first"][(region, gdr)]), r.choice(pool["last"][region]), gdr, region


class Person:
    __slots__ = ("first", "last", "gender", "region", "source", "email", "phone10", "emp_id", "mono")

    def __init__(self, first, last, gender, region, source, email, phone10, emp_id=None, mono=False):
        self.first, self.last, self.gender, self.region = first, last, gender, region
        self.source, self.email, self.phone10, self.emp_id, self.mono = source, email, phone10, emp_id, mono

    @property
    def full(self):
        return self.first if self.mono or not self.last else f"{self.first} {self.last}"


def slug(s):
    return "".join(c for c in s.lower() if c.isalpha())


def rand_mobile(r):
    return r.choices("6789", [8, 22, 30, 40])[0] + "".join(r.choice(string.digits) for _ in range(9))


def _emp_id(r, used):
    while True:
        k = r.random()
        if k < 0.70:
            v = f"EMP-{r.randint(1000, 99999):05d}"
        elif k < 0.85:
            v = f"KS/{r.randint(12, 26):02d}/{r.randint(1, 9999):04d}"
        else:
            v = f"E{r.randint(1, 9999999):07d}"
        if v not in used:
            used.add(v)
            return v


def build_roster(seed, pools):
    """~1,500 people: 750 per split, names only from that split's roster pool."""
    r = random.Random(f"{seed}|roster2")
    out = {s: [] for s in SPLITS}
    names, emails, phones, ids = set(), set(), set(), set()
    for s in SPLITS:
        pool = pools[f"roster_{s}"]
        while len(out[s]) < 750:
            f, l, g, reg = draw_name(r, pool)
            if (f, l) in names:
                continue
            names.add((f, l))
            k = r.random()
            local = (f"{slug(f)}.{slug(l)}" if k < 0.6 else f"{slug(f)[0]}{slug(l)}" if k < 0.8
                     else f"{slug(f)}_{slug(l)[0]}" if k < 0.9 else f"{slug(f)}{slug(l)[:3]}")
            email, n = f"{local}@{TENANT_DOMAIN}", 2
            while email in emails:
                email = f"{local}{n}@{TENANT_DOMAIN}"
                n += 1
            emails.add(email)
            ph = rand_mobile(r)
            while ph in phones:
                ph = rand_mobile(r)
            phones.add(ph)
            out[s].append(Person(f, l, g, reg, "roster", email, ph, _emp_id(r, ids)))
    return out


def roster_rows(people):
    rows = [{"employee_id": p.emp_id, "full_name": p.full, "first_name": p.first, "last_name": p.last,
             "email": p.email, "phone": f"+91 {p.phone10[:5]} {p.phone10[5:]}"}
            for s in SPLITS for p in people[s]]
    rows.sort(key=lambda x: x["employee_id"])
    return rows


# --------------------------------------------------------------------------- value generators
DIG, UP, LOW = string.digits, string.ascii_uppercase, string.ascii_lowercase
ALNUM = string.ascii_letters + string.digits
PERSONAL_DOMAINS = ["gmail.com"] * 5 + ["yahoo.co.in", "yahoo.com", "outlook.com", "rediffmail.com", "hotmail.com",
                                        "proton.me", "icloud.com", "live.in"]
UPI_HANDLES = ["okhdfcbank", "okicici", "oksbi", "okaxis", "ybl", "ibl", "axl", "paytm", "apl", "upi",
               "fbl", "jupiter", "ikwik", "postbank", "pnb", "barodampay"]
BANKS = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PUNB", "BARB", "YESB", "IDIB", "CNRB", "UBIN", "IDFB",
         "INDB", "FDRL", "BKID", "RATN", "SIBL", "KARB", "CIUB", "MAHB"]
COMPANIES = ["Quantiva Systems", "Blue Lantern Logistics", "Meridian Payroll Services", "Orchid Staffing",
             "Vega Facility Management", "TalentBridge Consulting", "Northgate Analytics", "Ashvik Infra",
             "Zephyr Cloud Labs", "Lumina Insurance Brokers", "Trident Security Services", "Pinecrest Travels",
             "Saffron Catering Co", "Helix Med Diagnostics", "Corvus Recruitment", "Ironbark Engineering",
             "Solstice Learning", "Nimbus Data Works", "Kestrel Benefits Desk", "Marigold Uniforms",
             "Cobalt Couriers", "Aurum Wealth Advisors", "Terrapin Software", "Redwood Relocations"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def gen_aadhaar(r):
    p = r.choice("23456789") + "".join(r.choice(DIG) for _ in range(10))
    return p + verhoeff_check_digit(p)


def fmt_aadhaar(r, d, style=None):
    style = style if style is not None else r.choices(range(4), [40, 40, 14, 6])[0]
    if style == 0:
        return d
    sep = {1: " ", 2: "-", 3: "."}[style]
    return sep.join((d[:4], d[4:8], d[8:]))


def gen_pan(r, p=None):
    kind = r.choices("PCHFATG", [72, 10, 4, 3, 3, 4, 4])[0]
    ini = slug(p.last)[0].upper() if (p and p.last and kind == "P") else r.choice(UP)
    return "".join(r.choice(UP) for _ in range(3)) + kind + ini + "".join(r.choice(DIG) for _ in range(4)) + r.choice(UP)


def gen_acct(r):
    n = r.choices([9, 10, 11, 12, 13, 14, 15, 16, 17, 18], [4, 8, 10, 22, 8, 8, 14, 12, 4, 10])[0]
    return r.choice("123456789") + "".join(r.choice(DIG) for _ in range(n - 1))


def gen_ifsc(r):
    tail = "".join(r.choice(DIG) if r.random() < 0.85 else r.choice(UP) for _ in range(6))
    return r.choice(BANKS) + "0" + tail


def gen_card(r, style=None):
    k = r.random()
    if k < 0.45:
        pre, ln = "4", 16
    elif k < 0.72:
        pre, ln = str(r.randint(51, 55)), 16
    elif k < 0.90:
        pre, ln = r.choice(["60", "65", "81", "82", "508"]), 16
    else:
        pre, ln = r.choice(["34", "37"]), 15
    body = pre + "".join(r.choice(DIG) for _ in range(ln - len(pre) - 1))
    d = body + luhn_check_digit(body)
    style = style if style is not None else r.choices(range(4), [35, 40, 15, 10])[0]
    if style == 0:
        return d
    sep = {1: " ", 2: "-", 3: "/"}[style]
    if ln == 15:
        return sep.join((d[:4], d[4:10], d[10:]))
    return sep.join(d[i:i + 4] for i in range(0, 16, 4))


def gen_phone(r, p10=None, style=None):
    d = p10 or rand_mobile(r)
    style = style if style is not None else r.choices(range(12), [22, 8, 6, 12, 6, 6, 6, 5, 4, 4, 3, 2])[0]
    return [f"+91 {d[:5]} {d[5:]}", f"+91-{d}", f"0{d}", f"{d[:5]} {d[5:]}", f"{d[:5]}-{d[5:]}", f"+91{d}",
            f"(+91) {d[:5]} {d[5:]}", f"91 {d}", d, f"+91 {d[:3]} {d[3:6]} {d[6:]}", f"{d[:3]}-{d[3:6]}-{d[6:]}",
            f"+91 {d[:2]}-{d[2:6]}-{d[6:]}"][style]


def gen_phone_other(r):
    k = r.random()
    if k < 0.3:
        c = r.choice(["22", "11", "80", "44", "40", "33", "20"])
        rest = "".join(r.choice(DIG) for _ in range(8))
        rest = str(r.randint(2, 6)) + rest[1:]
        return r.choice([f"0{c}-{rest[:4]} {rest[4:]}", f"+91 {c} {rest[:4]} {rest[4:]}", f"({c}) {rest}"]) if len(c) == 2 else f"0{c}-{rest}"
    if k < 0.5:
        return f"+44 7{r.randint(100, 999)} {r.randint(100000, 999999)}"
    if k < 0.7:
        return f"+1 ({r.randint(201, 989)}) {r.randint(200, 999)}-{r.randint(1000, 9999)}"
    if k < 0.85:
        return f"+65 {r.choice('89')}{r.randint(100, 999)} {r.randint(1000, 9999)}"
    return f"+971 5{r.randint(0, 8)} {r.randint(100, 999)} {r.randint(1000, 9999)}"


def gen_upi(r, p=None, phone=False):
    h = r.choice(UPI_HANDLES)
    if phone or p is None:
        return f"{rand_mobile(r)}@{h}"
    k = r.random()
    f, l = slug(p.first), slug(p.last or p.first)
    loc = (f"{f}.{l}" if k < 0.35 else f"{f}{r.randint(1, 99)}" if k < 0.6 else f"{f[0]}{l}{r.randint(1, 999)}"
           if k < 0.8 else f"{p.phone10}" if k < 0.9 else f"{l}.{f}")
    return f"{loc}@{h}"


def gen_dob(r):
    y = r.randint(1962, 2003)
    return date(y, r.randint(1, 12), r.randint(1, 28))


def fmt_date(r, d, style=None):
    style = style if style is not None else r.choices(range(12), [20, 8, 14, 12, 8, 6, 6, 6, 5, 6, 4, 5])[0]
    m = d.month
    if style == 8:
        n = d.day
        suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suf} {MONTHS[m - 1]} {d.year}"
    return [d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%Y-%m-%d"), d.strftime("%d %b %Y"),
            d.strftime("%d %B %Y"), f"{MONTHS[m - 1]} {d.day}, {d.year}", d.strftime("%d.%m.%Y"),
            d.strftime("%d-%b-%Y"), "", d.strftime("%Y%m%d"), d.strftime("%d/%m/%y"),
            f"{d.day}-{MONTHS[m - 1][:3]}-{d.year}"][style]


def rand_date(r, y0=2015, y1=2026):
    a, b = date(y0, 1, 1), date(y1, 9, 1)
    return a + timedelta(days=r.randint(0, (b - a).days))


def gen_secret(r, kind=None, uri_safe=False):
    """-> (value, kind). Values contain no quotes, backslashes, whitespace, or XML/JSON-special characters."""
    kinds = ["aws_key", "aws_secret", "gh_pat", "slack_bot", "stripe", "jwt", "hex32", "hex40", "password",
             "b64", "sendgrid", "opaque", "google", "uuid", "password2"]
    kind = kind or ("password" if uri_safe else r.choice(kinds))
    if uri_safe and kind in ("aws_secret", "b64", "jwt"):
        kind = "password"
    if kind == "aws_key":
        return "AKIA" + "".join(r.choice(UP + DIG) for _ in range(16)), kind
    if kind == "aws_secret":
        return "".join(r.choice(ALNUM + "/+") for _ in range(40)), kind
    if kind == "gh_pat":
        return "ghp_" + "".join(r.choice(ALNUM) for _ in range(36)), kind
    if kind == "slack_bot":
        return (f"xoxb-{r.randint(10**11, 10**12 - 1)}-{r.randint(10**11, 10**12 - 1)}-"
                + "".join(r.choice(ALNUM) for _ in range(24))), kind
    if kind == "stripe":
        return r.choice(["sk_live_", "rk_live_", "sk_test_"]) + "".join(r.choice(ALNUM) for _ in range(24)), kind
    if kind == "jwt":
        u = ALNUM + "-_"
        return ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "eyJ" + "".join(r.choice(u) for _ in range(r.randint(40, 90)))
                + "." + "".join(r.choice(u) for _ in range(43))), kind
    if kind == "hex32":
        return "".join(r.choice("0123456789abcdef") for _ in range(32)), kind
    if kind == "hex40":
        return "".join(r.choice("0123456789abcdef") for _ in range(40)), kind
    if kind == "b64":
        return "".join(r.choice(ALNUM + "+/") for _ in range(r.choice([32, 44, 48]))) + "=", kind
    if kind == "sendgrid":
        return "SG." + "".join(r.choice(ALNUM + "-_") for _ in range(22)) + "." + "".join(r.choice(ALNUM + "-_") for _ in range(43)), kind
    if kind == "opaque":
        return "".join(r.choice(ALNUM) for _ in range(r.choice([32, 40, 64]))), kind
    if kind == "google":
        return "AIza" + "".join(r.choice(ALNUM + "-_") for _ in range(35)), kind
    if kind == "uuid":
        h = "0123456789abcdef"
        return "-".join("".join(r.choice(h) for _ in range(n)) for n in (8, 4, 4, 4, 12)), kind
    words = ["Monsoon", "Falcon", "Payroll", "Welcome", "Tiger", "Orchid", "Summit", "Kestrel", "Hrms", "Saffron",
             "Delta", "Marigold", "Quartz", "Lotus", "Mango", "Nimbus"]
    if kind == "password2":
        return r.choice(words).lower() + "-" + r.choice(words).lower() + "-" + str(r.randint(10, 9999)), kind
    sym = r.choice(["!", "#", "@", "$", "_", "!!", "*"]) if not uri_safe else r.choice(["!", "_", "-", ""])
    return r.choice(words) + sym + str(r.randint(10, 9999)) + (r.choice(["", "x", "!", "Z"]) if not uri_safe else ""), "password"


def gen_money(r, kind="annual", style=None):
    """Compensation amounts (span = currency + amount + unit like lakh/LPA, never the period words)."""
    if kind == "annual":
        n = r.randint(30, 6500) * 1000
    elif kind == "monthly":
        n = r.randint(8, 400) * 1000 + r.choice([0, 0, 500])
    elif kind == "bonus":
        n = r.randint(10, 900) * 1000
    else:
        n = r.randint(1, 60) * 100000
    style = style if style is not None else r.choices(range(9), [22, 12, 14, 10, 12, 8, 6, 6, 4])[0]
    lakh = n / 100000
    lk = (f"{int(lakh)}" if lakh == int(lakh) else f"{lakh:.1f}".rstrip("0").rstrip("."))
    if style == 0:
        return "₹" + inr_group(n)
    if style == 1:
        return "Rs. " + inr_group(n) + r.choice(["/-", "", ".00"])
    if style == 2:
        return "INR " + inr_group(n)
    if style == 3 and n >= 100000:
        return f"INR {lk} lakh" if r.random() < 0.5 else f"₹{lk} lakhs"
    if style == 4 and n >= 100000:
        return f"{lk} LPA"
    if style == 5:
        return f"₹{n // 1000}k" if n % 1000 == 0 else "₹" + inr_group(n)
    if style == 6:
        return r.choice(["USD ", "$", "US$"]) + west_group(n // 50 // 10 * 10 if n > 900000 else n // 40 // 10 * 10 + 10)
    if style == 7 and n >= 1000000:
        return f"₹{n / 10000000:.2f} crore".replace(".00", "")
    if style == 8:
        return "Rs " + inr_group(n)
    return "₹" + inr_group(n)


# --------------------------------------------------------------------------- field-name styles
FIELDS = {
    "AADHAAR": [["national", "id"], ["govt", "id", "number"], ["identity", "no"], ["aadhaar", "no"], ["uid"],
                ["aadhaar", "number"], ["uidai", "no"], ["national", "identifier"], ["gov", "id"], ["id", "proof", "no"]],
    "PAN": [["pan"], ["pan", "no"], ["pan", "number"], ["tax", "id"], ["income", "tax", "pan"],
            ["permanent", "account", "number"], ["tax", "identification", "no"]],
    "BANK_ACCOUNT": [["bank", "account", "number"], ["acct", "no"], ["account", "no"], ["beneficiary", "account"],
                     ["salary", "account"], ["bank", "ac", "no"], ["acc", "number"], ["payee", "account", "no"]],
    "IFSC": [["ifsc"], ["ifsc", "code"], ["bank", "ifsc"], ["branch", "ifsc"], ["routing", "code"], ["ifs", "code"]],
    "CARD": [["card", "number"], ["corp", "card", "no"], ["card", "no"], ["credit", "card"], ["payment", "card", "number"],
             ["cc", "num"]],
    "UPI_ID": [["upi", "id"], ["upi"], ["vpa"], ["payment", "address"], ["upi", "handle"]],
    "EMAIL": [["email"], ["work", "email"], ["e", "mail", "address"], ["email", "id"], ["official", "email"], ["mail"],
              ["contact", "email"], ["email", "address"]],
    "PHONE": [["phone"], ["mobile"], ["mobile", "no"], ["contact", "number"], ["cell", "phone"], ["msisdn"], ["tel"],
              ["phone", "number"], ["mobile", "number"]],
    "DATE_OF_BIRTH": [["dob"], ["date", "of", "birth"], ["birth", "date"], ["birthdate"], ["date", "birth"],
                      ["d", "o", "b"], ["born", "on"]],
    "EMPLOYEE_ID": [["emp", "id"], ["employee", "id"], ["employee", "number"], ["worker", "id"], ["person", "number"],
                    ["staff", "code"], ["emp", "code"], ["pernr"], ["personnel", "no"], ["associate", "id"]],
    "COMPENSATION": [["ctc"], ["annual", "ctc"], ["base", "salary"], ["gross", "salary"], ["basic", "pay"],
                     ["annual", "salary"], ["fixed", "comp"], ["total", "comp"], ["monthly", "gross"], ["variable", "pay"],
                     ["bonus", "amount"], ["net", "pay"]],
    "SECRET": [["password"], ["api", "key"], ["secret"], ["client", "secret"], ["access", "token"], ["auth", "token"],
               ["passwd"], ["service", "key"], ["private", "token"]],
    "PERSON": [["full", "name"], ["name"], ["employee", "name"], ["display", "name"], ["legal", "name"],
               ["worker", "name"], ["reporting", "manager"], ["nominee", "name"], ["contact", "name"]],
}


def style_name(words, style):
    ws = list(words)
    if style == "snake":
        return "_".join(ws)
    if style == "camel":
        return ws[0] + "".join(w.capitalize() for w in ws[1:])
    if style == "pascal":
        return "".join(w.capitalize() for w in ws)
    if style == "upper":
        return "_".join(w.upper() for w in ws)
    if style == "kebab":
        return "-".join(ws)
    if style == "dot":
        return ".".join(ws)
    if style == "title":
        return " ".join(w.capitalize() if len(w) > 3 else w.upper() for w in ws)
    if style == "pascal_us":
        return "_".join(w.capitalize() for w in ws)
    raise ValueError(style)


STYLES = ["snake", "camel", "pascal", "upper", "kebab", "dot", "title", "pascal_us"]


# --------------------------------------------------------------------------- template registry
TEMPLATES: dict[str, dict] = {s: {} for s in SLICES}
NER_TEMPLATES: dict[str, object] = {}


def ner_tpl(tid):
    def deco(fn):
        assert tid not in NER_TEMPLATES, tid
        fn.tid, fn.slice, fn.weight = tid, "ner", 1
        NER_TEMPLATES[tid] = fn
        return fn
    return deco


def tpl(slice_, tid, w=1):
    def deco(fn):
        assert tid not in TEMPLATES[slice_], tid
        fn.tid, fn.slice, fn.weight = tid, slice_, w
        TEMPLATES[slice_][tid] = fn
        return fn
    return deco


class G:
    """Per-item generation context handed to templates."""

    def __init__(self, rng, split, roster, ext_pool, state, ner=False):
        self.r, self.split, self.roster, self.ext_pool, self.state, self.ner = rng, split, roster, ext_pool, state, ner
        self.b = Builder()
        self.style = rng.choice(STYLES)      # default naming style for field names in this item

    # ---- people
    def person(self, src=None):
        st, r = self.state, self.r
        if self.ner:
            src = "external"
        if src is None:
            share = st["ext"] / st["n"] if st["n"] > 30 else 0.0
            src = "external" if r.random() < (0.62 if share < 0.42 else 0.28) else "roster"
        st["n"] += 1
        if src == "roster":
            return r.choice(self.roster)
        st["ext"] += 1
        f, l, g, reg = draw_name(r, self.ext_pool, NER_REGION_WEIGHTS if self.ner else None)
        mono = r.random() < (0.10 if self.ner else 0.05)
        ph = rand_mobile(r)
        p = Person(f, l, g, reg, "external", None, ph, None, mono)
        p.email = self.ext_email(p)
        return p

    def ext_email(self, p, corp=None):
        r = self.r
        f, l = slug(p.first), slug(p.last or p.first)
        k = r.random()
        loc = (f"{f}.{l}" if k < 0.35 else f"{f}{l}{r.randint(1, 99)}" if k < 0.55 else f"{f[0]}{l}" if k < 0.7
               else f"{f}_{l}" if k < 0.8 else f"{l}.{f}{r.randint(70, 99)}" if k < 0.9 else f"{f}{r.randint(1, 9999)}")
        return f"{loc}@{corp or r.choice(PERSONAL_DOMAINS)}"

    def corp(self):
        c = self.r.choice(COMPANIES)
        return c, slug(c.split()[0]) + self.r.choice([".com", ".in", ".co.in", ".io"])

    def nm(self, p, form="full"):
        f, l = p.first, p.last
        if p.mono or not l:
            form = "first" if form in ("full", "init", "init2", "lf", "last", "mid") else form
            text = {"first": f, "upper": f.upper(), "lower": f.lower()}.get(form, f)
            return E(text, "PERSON", p.source)
        if form == "full":
            t = f"{f} {l}"
        elif form == "first":
            t = f
        elif form == "last":
            t = l
        elif form == "init":
            t = f"{f[0]}. {l}"
        elif form == "init2":
            t = f"{f[0]} {l}"
        elif form == "lf":
            t = f"{l}, {f}"
        elif form == "upper":
            t = f"{f} {l}".upper()
        elif form == "lower":
            t = f"{f} {l}".lower()
        elif form == "mid":
            t = f"{f} {self.r.choice(UP)}. {l}"
        elif form == "init3":
            t = f"{f[0]}. {self.r.choice(UP)}. {l}"
        elif form == "lfu":
            t = f"{l} {f}".upper()
        else:
            raise ValueError(form)
        return E(t, "PERSON", p.source)

    def hon(self, p):
        return self.r.choice({"M": ["Mr. ", "Mr ", "Shri ", "Dr. "], "F": ["Ms. ", "Mrs. ", "Smt. ", "Ms ", "Dr. "]}[p.gender])

    # ---- field names
    def fld(self, t, style=None):
        return style_name(self.r.choice(FIELDS[t]), style or self.style)

    # ---- values
    def e(self, t, **kw):
        return E(self.v(t, **kw), t)

    def v(self, t, p=None, **kw):
        r = self.r
        if t == "AADHAAR":
            return fmt_aadhaar(r, gen_aadhaar(r), kw.get("style"))
        if t == "PAN":
            return gen_pan(r, p)
        if t == "BANK_ACCOUNT":
            return gen_acct(r)
        if t == "IFSC":
            return gen_ifsc(r)
        if t == "CARD":
            return gen_card(r, kw.get("style"))
        if t == "UPI_ID":
            return gen_upi(r, p, kw.get("phone", False))
        if t == "PHONE":
            if p is not None:
                return gen_phone(r, p.phone10, kw.get("style"))
            return gen_phone(r, None, kw.get("style"))
        if t == "DATE_OF_BIRTH":
            return fmt_date(r, kw.get("date") or gen_dob(r), kw.get("style"))
        if t == "COMPENSATION":
            return gen_money(r, kw.get("kind", "annual"), kw.get("style"))
        if t == "SECRET":
            return gen_secret(r, kw.get("kind"), kw.get("uri_safe", False))[0]
        raise ValueError(t)

    def phone(self, p=None, style=None):
        return E(gen_phone(self.r, p.phone10 if p else None, style), "PHONE")

    def money(self, kind="annual", style=None):
        return E(gen_money(self.r, kind, style), "COMPENSATION")

    def dob(self, style=None, d=None):
        return E(fmt_date(self.r, d or gen_dob(self.r), style), "DATE_OF_BIRTH")

    def secret(self, kind=None, uri_safe=False):
        return E(gen_secret(self.r, kind, uri_safe)[0], "SECRET")

    def mail(self, p):
        return E(p.email, "EMAIL")

    def date(self, y0=2015, y1=2026, style=None):
        return fmt_date(self.r, rand_date(self.r, y0, y1), style)

    def pick(self, *xs):
        return self.r.choice(xs)

    def ts(self, sep="T", z=True):
        r = self.r
        d = rand_date(r, 2025, 2026)
        s = f"{d.isoformat()}{sep}{r.randint(0, 23):02d}:{r.randint(0, 59):02d}:{r.randint(0, 59):02d}"
        return s + (r.choice([".", ","]) + f"{r.randint(0, 999):03d}" if r.random() < 0.5 else "") + ("Z" if z and sep == "T" else "")
