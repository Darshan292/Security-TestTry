"""Synthetic value generators with the correct structure of each Indian HR identifier.

Everything draws from an explicit `random.Random`, so a seed fixes the output. Nothing here is a real person's data:
names are combined from public first-name and surname lists, numbers are generated (checksums computed), and company
names are invented. Hard negatives (values that look like PII but are not) live in the second half of the module.
"""
from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass

# ------------------------------------------------------------------------------------------------- checksums

_VD = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
       (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
       (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
       (9, 8, 7, 6, 5, 4, 3, 2, 1, 0))
_VP = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
       (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
       (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8))
_VINV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VD[c][_VP[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(payload: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(payload)):
        c = _VD[c][_VP[(i + 1) % 8][int(ch)]]
    return str(_VINV[c])


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def luhn_check_digit(payload: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _upper(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(n))


def _alnum_upper(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(n))


# ------------------------------------------------------------------------------------------------------ names

# (first names, surnames, gender flags). Public, commonly used names; Latin transliteration only. Grouped by
# community so every style of name appears; "region" is a sampling bucket, not an inference about anyone.
_M = "m"
_F = "f"
_FIRST: dict[str, list[tuple[str, str]]] = {
    "north_hindu": [(n, _M) for n in "Rahul Amit Vikas Rajesh Sandeep Anil Manoj Sanjay Deepak Ashok Pankaj Naveen Rohit Vivek "
                    "Ajay Mukesh Sunil Pradeep Yogesh Gaurav Harish Kamal Lokesh Nitin Rakesh Suresh Tarun Varun Ishaan Aarav "
                    "Kunal Mohit Tushar Ankit Shubham Akhilesh Dharmendra Brijesh Ramesh Dinesh".split()]
    + [(n, _F) for n in "Sunita Anita Neha Pooja Priya Kavita Rekha Seema Meena Ritu Shweta Swati Nidhi Anjali Divya Poonam "
       "Sapna Ranjana Kiran Madhu Shilpa Pallavi Komal Aarti Bharti Isha Jyoti Lata Manju Nisha Radha Savita Usha".split()],
    "south_hindu": [(n, _M) for n in "Venkatesh Suresh Karthik Arun Ganesh Srinivas Murali Ramakrishnan Balaji Harish Vignesh "
                    "Gopal Krishnan Senthil Naresh Prasad Raghavan Sundar Vijay Mahesh Satish Anand Bhaskar Chandrasekhar "
                    "Dilip Hari Jagadish Kishore Lakshman Narayanan Pavan Raju Sai Teja Venu Yashwanth".split()]
    + [(n, _F) for n in "Lakshmi Meenakshi Kavitha Sangeetha Revathi Padmavathi Vasanthi Deepa Sowmya Anitha Bhavani Chitra "
       "Gayathri Hema Indira Jayanthi Kalyani Latha Malathi Nirmala Pushpa Rajeshwari Savithri Uma Vidya Yamini Swathi".split()],
    "bengali": [(n, _M) for n in "Subhash Sourav Debashish Abhijit Arnab Partha Soumen Tapan Indranil Prasenjit Ranjit Sudipta "
                "Anirban Biswajit Dipankar Kaushik Pritam Rajib Sambit Tanmoy".split()]
    + [(n, _F) for n in "Moumita Sharmistha Rupa Tanushree Debjani Madhumita Mousumi Paramita Sagarika Swapna Piyali Ipsita "
       "Nandini Sutapa Aparna Bani Chandana".split()],
    "marathi_gujarati": [(n, _M) for n in "Sachin Prashant Nilesh Rahul Mangesh Swapnil Omkar Ketan Hitesh Jignesh Chirag Bhavesh "
                         "Dhaval Kalpesh Mitesh Nirav Paresh Rajan Tejas Vishal".split()]
    + [(n, _F) for n in "Snehal Manasi Rutuja Sayali Vaishali Pratibha Jaya Hetal Krupa Mital Nayna Purvi Rupal Dipika Bhakti "
       "Gauri Mugdha Smita".split()],
    "punjabi_sikh": [(n, _M) for n in "Harpreet Gurpreet Jaswinder Manpreet Balwinder Gurmeet Amarjeet Kuldeep Navdeep Randeep "
                     "Sukhdev Tejinder Charanjit Davinder Ravinder Satnam Inderjit".split()]
    + [(n, _F) for n in "Harpreet Gurleen Jasleen Manjeet Navneet Rupinder Simran Amrita Kiranjot Paramjit Sukhwinder Tanvi".split()],
    "muslim": [(n, _M) for n in "Mohammed Imran Irfan Salman Faisal Arif Asif Javed Nadeem Rashid Shahid Tariq Zaheer Aamir "
               "Farhan Iqbal Junaid Khalid Mustafa Sameer Wasim Yusuf Zubair".split()]
    + [(n, _F) for n in "Ayesha Fatima Nazia Shabana Farah Rukhsar Sana Shaista Tabassum Zeenat Afreen Bushra Mehnaz "
       "Nasreen Rubina Samina Yasmin Zoya".split()],
    "christian": [(n, _M) for n in "Joseph Thomas George Samuel Joshua Abraham Mathew Jacob Paul Philip Simon Cyril Francis "
                  "Alwyn Dominic Clifford Gerard Melvin Rodney Vincent".split()]
    + [(n, _F) for n in "Mary Anna Grace Rachel Sheela Merlin Jessy Priscilla Sheeba Teresa Beena Lizy Maria Elizabeth "
       "Flavia Jacinta Lourdes Rosy Shirley".split()],
    "jain_parsi_other": [(n, _M) for n in "Rohan Hemant Jinesh Nimesh Pranav Cyrus Darius Farokh Jehangir Rustom Zubin "
                         "Bomi Kersi Sorab Ashish Tenzin Lhakpa Bikram Dhiraj Kishan".split()]
    + [(n, _F) for n in "Roshni Dilnavaz Perin Shernaz Zarine Khushnum Nargis Tripti Anamika Mamta Pema Dolma Ngawang "
       "Binita Sushmita Mridula".split()],
    "northeast": [(n, _M) for n in "Bikash Pranjal Rupam Jitu Dhruba Kamal Mridul Nabajyoti Rajib Lalnunsanga Zothan Kimsanga".split()]
    + [(n, _F) for n in "Bornali Dipika Juri Mitali Monjita Pallabi Rashmi Lalremi Zothansangi Hmingi Banani".split()],
}
_SURNAMES: dict[str, list[str]] = {
    "north_hindu": "Sharma Verma Gupta Agarwal Mishra Tiwari Pandey Srivastava Chauhan Yadav Joshi Saxena Kapoor Malhotra Bansal "
                   "Garg Jain Mehra Khanna Arora Bhatia Dubey Tripathi Rawat Negi Bisht Thakur Chaturvedi Dwivedi".split(),
    "south_hindu": "Iyer Iyengar Nair Menon Pillai Reddy Naidu Rao Murthy Krishnan Subramanian Raman Venkataraman Gowda Shetty "
                   "Hegde Bhat Kamath Acharya Chandran Narayanan Sundaram Varma Prabhu Kulkarni Patil".split(),
    "bengali": "Banerjee Chatterjee Mukherjee Bose Ghosh Das Dutta Sen Roy Chakraborty Bhattacharya Sarkar Mondal Biswas "
               "Majumdar Ganguly Saha Dey Pal Mitra Basu Haldar".split(),
    "marathi_gujarati": "Patil Deshmukh Kulkarni Joshi Pawar Shinde Jadhav More Gaikwad Bhosale Patel Shah Mehta Desai Modi "
                        "Trivedi Parekh Thakkar Vyas Gandhi Doshi Zaveri".split(),
    "punjabi_sikh": "Singh Kaur Gill Sandhu Dhillon Sidhu Brar Grewal Bajwa Randhawa Virk Cheema Sekhon Walia Chopra Bedi Sethi".split(),
    "muslim": "Khan Ansari Siddiqui Qureshi Sheikh Syed Mirza Pathan Ahmed Hussain Ali Rahman Malik Chaudhary Baig Faruqui Kazmi".split(),
    "christian": "Fernandes D'Souza Pereira Dias Rodrigues Gomes Lobo Mascarenhas Menezes Thomas Mathew Joseph Varghese George "
                 "Abraham Philip Cherian D'Mello Colaco".split(),
    "jain_parsi_other": "Bhandari Oswal Daga Chopra Mehta Bafna Irani Contractor Engineer Wadia Bharucha Mistry Dastur "
                        "Lama Bhutia Sherpa Tamang Gurung Thapa".split(),
    "northeast": "Borah Baruah Gogoi Hazarika Saikia Bora Das Sangma Marak Lyngdoh Syiem Kharbuli Ao Angami Sema Zoramthanga".split(),
}
_INITIAL_FIRST = "ABCDEGHJKLMNPRSTVY"
REGIONS = tuple(sorted(_FIRST))


@dataclass(frozen=True)
class Person:
    first: str
    last: str
    middle: str = ""
    gender: str = "m"
    region: str = "north_hindu"

    def full(self) -> str:
        return " ".join(p for p in (self.first, self.middle, self.last) if p)


# ------------------------------------------------------------------------------- optional Faker (en_IN) source
# Extra diversity for person names and address lines, behind a flag and OFF by default: with it off, `person()` consumes
# exactly the same random numbers as before, so every existing seed reproduces its old output. Faker (MIT) is not
# used for any identifier: its en_IN `aadhaar_id` is Luhn-valid (not Verhoeff: ~9% of its numbers are valid Aadhaars,
# and it emits leading 0/1), so Aadhaar, PAN, GSTIN and every other number stay with this module's generators
# (tests/ml_train/test_faker_source.py documents the measurements).
FAKER_SHARE = 0.0
_FAKER = None
_NAME_OK = re.compile(r"[A-Za-z][A-Za-z'-]{1,24}")


def enable_faker(share: float = 0.3, locale: str = "en_IN") -> None:
    """Draw about `share` of all persons (and address blocks) from Faker. Raises ImportError if faker is missing."""
    global FAKER_SHARE, _FAKER
    if not 0.0 <= share <= 1.0:
        raise ValueError("share must be in [0, 1]")
    if share > 0 and (_FAKER is None or _FAKER.locales != [locale]):
        from faker import Faker
        _FAKER = Faker(locale)
    FAKER_SHARE = float(share)


def disable_faker() -> None:
    global FAKER_SHARE
    FAKER_SHARE = 0.0


def _faker(rng: random.Random):
    """The shared Faker instance, re-seeded from `rng` so the result depends only on rng's state."""
    _FAKER.seed_instance(rng.getrandbits(64))
    return _FAKER


def faker_person(rng: random.Random) -> Person | None:
    """A Person from Faker's en_IN name lists (ASCII only), or None if it produced nothing usable."""
    fk = _faker(rng)
    male = rng.random() < 0.5
    for _ in range(6):
        first = fk.first_name_male() if male else fk.first_name_female()
        last = fk.last_name()
        if _NAME_OK.fullmatch(first) and _NAME_OK.fullmatch(last) and first.lower() != last.lower():
            return Person(first, last, "", _M if male else _F, "faker_en_in")
    return None


def faker_address(rng: random.Random) -> tuple[str, str]:
    """Two address lines (street/house, city and PIN) in the style of `address_block`. They are unlabelled context: an
    address is not a gateway entity type, and Faker's street names reuse surnames, which doubles as a hard negative for
    PERSON (a surname-looking word in a place name must not be masked as a person)."""
    fk = _faker(rng)
    line1 = f"{rng.choice(('Flat', 'H.No.', 'Door No.'))} {rng.randint(1, 99)}/{rng.randint(1, 40)}, {fk.street_name()},"
    return line1, f"{fk.city()} - {fk.postcode()}"


def person(rng: random.Random, region: str | None = None) -> Person:
    if FAKER_SHARE > 0.0 and region is None and rng.random() < FAKER_SHARE:
        fp = faker_person(rng)
        if fp is not None:
            return fp
    region = region or rng.choice(REGIONS)
    first, gender = rng.choice(_FIRST[region])
    last = rng.choice(_SURNAMES[region])
    middle = ""
    if rng.random() < 0.12:
        middle = rng.choice(_FIRST[region])[0]
        if rng.random() < 0.5:
            middle = rng.choice(_FIRST[region])[0] + "."
    if region == "punjabi_sikh" and rng.random() < 0.7:
        last = "Kaur" if gender == _F else "Singh"
    if region == "south_hindu" and rng.random() < 0.35:
        last = rng.choice(_INITIAL_FIRST)  # south Indian single-letter patronymic initial: "Ramesh K"
    if region == "marathi_gujarati" and rng.random() < 0.1:
        middle = rng.choice(_FIRST[region])[0]
    return Person(first, last, middle, gender, region)


def name_surface(rng: random.Random, p: Person, style: str | None = None) -> str:
    """Render a person in one of the ways names appear in HR text. No honorific: titles are context, not the name."""
    styles = ("full", "full", "full", "last_first", "initial_last", "caps", "first_only", "lower", "first_initial")
    style = style or rng.choice(styles)
    last = p.last
    if style == "full":
        return p.full()
    if style == "last_first":
        return f"{last}, {p.first}" if len(last) > 1 else f"{p.first} {last}"
    if style == "initial_last":
        return f"{p.first[0]}. {last}" if len(last) > 1 else f"{last}. {p.first}"
    if style == "caps":
        return p.full().upper()
    if style == "first_only":
        return p.first
    if style == "lower":
        return p.full().lower()
    if style == "first_initial":
        return f"{p.first} {last[0]}."
    if style == "last":
        return last
    if style == "initial":
        return f"{p.first[0]}."
    raise ValueError(f"unknown name style {style!r}")


# --------------------------------------------------------------------------------------------------- id schemes

_PAN_HOLDER = "PCHFATBLJG"  # 4th character: P individual, C company, H HUF, F firm, A AOP, T trust, B BOI, L local, J juridical, G govt
_BANK_CODES = ("SBIN", "HDFC", "ICIC", "UTIB", "PUNB", "BARB", "CNRB", "KKBK", "IDIB", "UBIN", "YESB", "INDB", "FDRL", "MAHB", "BKID")
_UPI_BANKS = ("okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "ibl", "axl", "paytm", "upi", "apl", "sbi", "icici", "hdfcbank",
              "pnb", "barodampay", "fbl", "ikwik", "airtel")
_EMAIL_FREE = ("gmail.com", "gmail.com", "gmail.com", "yahoo.co.in", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "proton.me")
_COMPANY_WORDS = ("Nimbus", "Kestrel", "Altura", "Bluepine", "Corvid", "Dhanvi", "Everlane", "Fintrail", "Girnar", "Harbor",
                  "Indigo Leaf", "Jaltarang", "Kavach", "Lumen", "Meridian", "Nakshatra", "Orchid", "Prakriti", "Quanta",
                  "Rasa", "Sagar", "Tarang", "Udaan", "Vistara Labs", "Waypoint", "Zenith")
_COMPANY_SUFFIX = ("Technologies", "Solutions", "Systems", "Industries", "Logistics", "Pharma", "Retail", "Finserv", "Labs")


def company(rng: random.Random) -> tuple[str, str]:
    """(display name, domain stem) of an invented company."""
    w = rng.choice(_COMPANY_WORDS)
    s = rng.choice(_COMPANY_SUFFIX)
    stem = w.lower().replace(" ", "")
    return f"{w} {s} Pvt Ltd" if rng.random() < 0.6 else f"{w} {s}", stem


def aadhaar(rng: random.Random, fmt: str | None = None) -> str:
    """12 digits, first digit 2-9, Verhoeff check digit. Formats: plain, spaced (4-4-4), hyphen, dot."""
    payload = rng.choice("23456789") + _digits(rng, 10)
    d = payload + verhoeff_check_digit(payload)
    fmt = fmt or rng.choice(("spaced", "spaced", "plain", "hyphen", "dot"))
    if fmt == "plain":
        return d
    sep = {"spaced": " ", "hyphen": "-", "dot": "."}[fmt]
    return sep.join((d[:4], d[4:8], d[8:]))


def pan(rng: random.Random, surname: str | None = None, holder: str | None = None) -> str:
    """AAAAA9999A: first three letters sequence, 4th = holder type, 5th = first letter of surname (individuals) or of
    the entity name, then 4 digits and a check letter."""
    holder = holder or ("P" if rng.random() < 0.9 else rng.choice(_PAN_HOLDER))
    fifth = (surname or "")[:1].upper()
    if not fifth.isalpha() or not fifth.isascii():
        fifth = rng.choice(string.ascii_uppercase)
    return f"{_upper(rng, 3)}{holder}{fifth}{_digits(rng, 4)}{_upper(rng, 1)}"


def ifsc(rng: random.Random) -> str:
    return rng.choice(_BANK_CODES) + "0" + _alnum_upper(rng, 6)


def card(rng: random.Random, fmt: str | None = None, network: str | None = None) -> str:
    network = network or rng.choice(("visa", "visa", "mastercard", "mastercard", "rupay", "amex"))
    if network == "visa":
        body, n = "4" + _digits(rng, 14), 16
    elif network == "mastercard":
        body, n = rng.choice(("51", "52", "53", "54", "55")) + _digits(rng, 13), 16
    elif network == "rupay":
        body, n = rng.choice(("60", "65", "81", "82", "508")) + _digits(rng, 13), 16
        body = body[:15]
    else:
        body, n = rng.choice(("34", "37")) + _digits(rng, 12), 15
    d = body + luhn_check_digit(body)
    assert len(d) == n and luhn_valid(d)
    fmt = fmt or rng.choice(("plain", "spaced", "spaced", "hyphen"))
    if fmt == "plain":
        return d
    sep = " " if fmt == "spaced" else "-"
    if n == 15:
        return sep.join((d[:4], d[4:10], d[10:]))
    return sep.join(d[i:i + 4] for i in range(0, 16, 4))


def bank_account(rng: random.Random, length: int | None = None, fmt: str | None = None) -> str:
    n = length or rng.choice((9, 10, 11, 12, 12, 13, 14, 15, 15, 16, 17, 18))
    if not 9 <= n <= 18:
        raise ValueError("bank account numbers are 9-18 digits")
    d = rng.choice("123456789") + _digits(rng, n - 1) if rng.random() < 0.9 else "0" + _digits(rng, n - 1)
    if (fmt or rng.choice(("plain",) * 6 + ("spaced",))) == "spaced":
        return " ".join(d[i:i + 4] for i in range(0, n, 4))
    return d


def upi_id(rng: random.Random, p: Person | None = None, phone: str | None = None) -> str:
    bank = rng.choice(_UPI_BANKS)
    r = rng.random()
    if phone and r < 0.3:
        handle = phone[-10:]
    elif p is not None and r < 0.8:
        handle = rng.choice((p.first.lower() + p.last.lower(), f"{p.first.lower()}.{p.last.lower()}",
                             f"{p.first.lower()}{rng.randint(1, 99)}", f"{p.first[0].lower()}{p.last.lower()}{rng.randint(1, 999)}"))
    else:
        handle = rng.choice(("m", "r", "s")) + _digits(rng, 10)
    handle = "".join(c for c in handle if c.isalnum() or c in "._-") or "user"
    return f"{handle}@{bank}"


def phone(rng: random.Random, fmt: str | None = None) -> str:
    mob = rng.choice("6789") + _digits(rng, 9)
    fmt = fmt or rng.choice(("plain", "plus91", "plus91_space", "plus91_hyphen", "zero", "split5", "paren", "plus91_nosp",
                             "plus91_space", "landline"))
    if fmt == "plain":
        return mob
    if fmt == "plus91":
        return f"+91 {mob}"
    if fmt == "plus91_space":
        return f"+91 {mob[:5]} {mob[5:]}"
    if fmt == "plus91_hyphen":
        return f"+91-{mob}"
    if fmt == "plus91_nosp":
        return f"+91{mob}"
    if fmt == "zero":
        return f"0{mob}" if rng.random() < 0.5 else f"0{mob[:5]} {mob[5:]}"
    if fmt == "split5":
        return f"{mob[:5]}-{mob[5:]}" if rng.random() < 0.5 else f"{mob[:5]} {mob[5:]}"
    if fmt == "paren":
        return f"(+91) {mob[:5]} {mob[5:]}"
    if fmt == "landline":
        std = rng.choice(("011", "022", "033", "040", "044", "080", "020", "079"))
        num = rng.choice("23456") + _digits(rng, 7)
        return f"{std}-{num[:4]} {num[4:]}" if rng.random() < 0.5 else f"{std}-{num}"
    raise ValueError(f"unknown phone format {fmt!r}")


def email(rng: random.Random, p: Person | None = None, corporate: bool | None = None) -> str:
    p = p or person(rng)
    f, l = _ascii(p.first), _ascii(p.last)
    corporate = rng.random() < 0.5 if corporate is None else corporate
    local = rng.choice((f"{f}.{l}", f"{f}_{l}", f"{f[0]}{l}", f"{f}{l}", f"{f}.{l[0]}", f"{f}{rng.randint(1, 99)}",
                        f"{f}.{l}{rng.randint(1, 999)}", f"{l}.{f}"))
    if corporate:
        _, stem = company(rng)
        domain = f"{stem}.{rng.choice(('com', 'in', 'co.in'))}"
    else:
        domain = rng.choice(_EMAIL_FREE)
    return f"{local}@{domain}"


def _ascii(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum()) or "user"


_EMP_STYLES = ("EMP{n5}", "E{n4}", "{co}{n5}", "{co}-{n4}", "{co}/{city}/{n4}", "{n7}", "EID-{n6}", "{co}{yy}{n4}", "A{n5}",
               "{co}-{yy}-{n4}", "EMP-{n3}-{n3}", "{co}{n6}")
_CITY3 = ("BLR", "HYD", "PUN", "MUM", "DEL", "CHN", "KOL", "AHD", "NOI", "GGN")


def employee_id(rng: random.Random, style: str | None = None, co: str | None = None) -> str:
    style = style or rng.choice(_EMP_STYLES)
    co = co or (_upper(rng, rng.choice((2, 3))))
    return style.format(n3=_digits(rng, 3), n4=_digits(rng, 4), n5=_digits(rng, 5), n6=_digits(rng, 6), n7=_digits(rng, 7),
                        co=co, city=rng.choice(_CITY3), yy=rng.choice(("19", "20", "21", "22", "23", "24")))


def identifier(rng: random.Random, kind: str | None = None) -> str:
    """Identifier-shaped personal/document references of no recognised financial scheme (gateway type IDENTIFIER):
    passport, driving licence, voter id, UAN, EPF member id, insurance policy, ESIC number, vehicle registration."""
    kind = kind or rng.choice(("passport", "dl", "voter", "uan", "epf", "policy", "esic", "gstin_like"))
    if kind == "passport":
        return rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") + _digits(rng, 7)
    if kind == "dl":
        return f"{rng.choice(('KA', 'MH', 'DL', 'TN', 'UP', 'GJ', 'WB', 'RJ'))}{_digits(rng, 2)} {rng.randint(2005, 2023)}{_digits(rng, 7)}"
    if kind == "voter":
        return _upper(rng, 3) + _digits(rng, 7)
    if kind == "uan":
        return "1" + _digits(rng, 11)
    if kind == "epf":
        return f"{rng.choice(('MH', 'KA', 'DL', 'TN'))}/{_upper(rng, 3)}/{_digits(rng, 7)}/{_digits(rng, 3)}/{_digits(rng, 7)}"
    if kind == "policy":
        return f"{_digits(rng, 2)}{rng.choice(('P', 'L', 'H'))}{_digits(rng, 9)}"
    if kind == "esic":
        return _digits(rng, 17)
    if kind == "gstin_like":
        return f"{rng.randint(1, 37):02d}{pan(rng, holder='P')}{rng.randint(1, 9)}Z{_alnum_upper(rng, 1)}"
    raise ValueError(f"unknown identifier kind {kind!r}")


# ----------------------------------------------------------------------------------------------- money

def indian_group(n: int) -> str:
    """12345678 -> 1,23,45,678 (lakh/crore grouping)."""
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts + [tail])
    return "-" + out if n < 0 else out


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
         "eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _below100(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")


def number_words(n: int) -> str:
    """Indian-system words: 1250000 -> 'twelve lakh fifty thousand'."""
    if n == 0:
        return "zero"
    out = []
    for unit, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand"), (100, "hundred")):
        q, n = divmod(n, unit)
        if q:
            out.append(f"{_below100(q)} {name}")
    if n:
        out.append(_below100(n))
    return " ".join(out)


def compensation(rng: random.Random, style: str | None = None, kind: str | None = None) -> str:
    """An INR amount belonging to a person's pay. kind: 'monthly' | 'annual' | None (random)."""
    kind = kind or rng.choice(("monthly", "annual", "annual"))
    if kind == "monthly":
        amt = rng.randrange(8, 400) * 1000 + rng.choice((0, 0, 0, 500, 250, 750))
    else:
        amt = rng.randrange(2, 150) * 100_000 + rng.choice((0, 0, 0, 50_000, 25_000, 75_000, 12_000))
    styles = ("inr_symbol", "rs_dot", "rs_plain", "inr_code", "slash", "lakh_L", "lakhs", "lpa", "words", "k", "plain_group",
              "rs_slash", "western", "crore", "words_rupees", "decimal_paise")
    style = style or rng.choice(styles)
    g = indian_group(amt)
    if style == "inr_symbol":
        return f"₹{g}" if rng.random() < 0.6 else f"₹ {g}"
    if style == "rs_dot":
        return f"Rs. {g}" if rng.random() < 0.6 else f"Rs.{g}"
    if style == "rs_plain":
        return f"Rs {g}"
    if style == "inr_code":
        return f"INR {g}"
    if style == "slash":
        return f"{g}/-"
    if style == "rs_slash":
        return f"Rs. {g}/-"
    if style == "western":
        return f"Rs {amt:,}" if rng.random() < 0.5 else f"Rs. {amt}"
    if style == "decimal_paise":
        return f"₹{g}.{rng.choice(('00', '50', '25', '75'))}"
    if style in ("lakh_L", "lakhs", "lpa", "crore", "words", "words_rupees"):
        if kind == "monthly":
            amt = rng.randrange(2, 90) * 100_000 + rng.choice((0, 50_000))
        lakhs = amt / 100_000
        txt = f"{lakhs:g}"
        if style == "lakh_L":
            return rng.choice((f"{txt}L", f"{txt} L", f"₹{txt}L", f"Rs {txt}L"))
        if style == "lakhs":
            return f"{txt} {'lakh' if lakhs <= 1 else rng.choice(('lakhs', 'lakh'))}"
        if style == "lpa":
            return rng.choice((f"{txt} LPA", f"{txt}LPA", f"{txt} lpa", f"₹{txt} LPA", f"{txt} LPA".upper()))
        if style == "crore":
            return f"{lakhs / 100:g} Cr" if lakhs >= 100 else f"{txt} lakh"
        w = number_words(int(amt))
        return w if style == "words" else f"Rs {w}" if rng.random() < 0.5 else f"{w} rupees"
    if style == "k":
        return f"{amt // 1000}k" if amt % 1000 == 0 else f"{amt / 1000:g}k"
    return g  # plain_group


def _fmt_date(d: int, m: int, y: int, style: str, rng: random.Random) -> str:
    mn = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    ordn = "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return {
        "dmy_slash": f"{d:02d}/{m:02d}/{y}", "dmy_dash": f"{d:02d}-{m:02d}-{y}", "dmy_dot": f"{d:02d}.{m:02d}.{y}",
        "d_mon_y": f"{d} {mn[m - 1][:3]} {y}", "d_month_y": f"{d} {mn[m - 1]} {y}", "month_d_y": f"{mn[m - 1]} {d}, {y}",
        "ordinal": f"{d}{ordn} {mn[m - 1]} {y}", "iso": f"{y}-{m:02d}-{d:02d}", "dmy_short": f"{d:02d}/{m:02d}/{y % 100:02d}",
        "d_mon_yy": f"{d:02d}-{mn[m - 1][:3]}-{y % 100:02d}", "d_mon_y_dash": f"{d:02d}-{mn[m - 1][:3]}-{y}",
        "mdy_slash": f"{m:02d}/{d:02d}/{y}" if d > 12 else f"{d:02d}/{m:02d}/{y}",
    }[style]


DATE_STYLES = ("dmy_slash", "dmy_slash", "dmy_dash", "dmy_dot", "d_mon_y", "d_month_y", "month_d_y", "ordinal", "iso", "dmy_short",
               "d_mon_yy", "d_mon_y_dash", "mdy_slash")


def _rand_date(rng: random.Random, y0: int, y1: int) -> tuple[int, int, int]:
    y = rng.randint(y0, y1)
    m = rng.randint(1, 12)
    d = rng.randint(1, (28 if m == 2 else 30 if m in (4, 6, 9, 11) else 31))
    return d, m, y


def date_of_birth(rng: random.Random, style: str | None = None, minor: bool = False) -> str:
    d, m, y = _rand_date(rng, 2009, 2020) if minor else _rand_date(rng, 1962, 2004)
    return _fmt_date(d, m, y, style or rng.choice(DATE_STYLES), rng)


def other_date(rng: random.Random, style: str | None = None) -> str:
    """A date that is NOT a date of birth (joining, effective, payment, deadline)."""
    d, m, y = _rand_date(rng, 2015, 2026)
    return _fmt_date(d, m, y, style or rng.choice(DATE_STYLES), rng)


def secret(rng: random.Random, kind: str | None = None) -> str:
    kind = kind or rng.choice(("sk", "aws", "ghp", "slack", "google", "password", "password", "password", "hex", "jwt", "pat"))
    a62 = string.ascii_letters + string.digits
    if kind == "sk":
        return "sk-" + "".join(rng.choice(a62) for _ in range(48))
    if kind == "aws":
        return "AKIA" + _alnum_upper(rng, 16)
    if kind == "ghp":
        return "ghp_" + "".join(rng.choice(a62) for _ in range(36))
    if kind == "slack":
        return f"xoxb-{_digits(rng, 11)}-{_digits(rng, 12)}-" + "".join(rng.choice(a62) for _ in range(24))
    if kind == "google":
        return "AIza" + "".join(rng.choice(a62 + "-_") for _ in range(35))
    if kind == "hex":
        return "".join(rng.choice("0123456789abcdef") for _ in range(rng.choice((32, 40, 64))))
    if kind == "jwt":
        seg = lambda n: "".join(rng.choice(a62 + "-_") for _ in range(n))  # noqa: E731
        return f"eyJ{seg(30)}.eyJ{seg(50)}.{seg(43)}"
    if kind == "pat":
        return "".join(rng.choice(a62) for _ in range(52))
    if kind == "password":
        word = rng.choice(("Welcome", "Hr", "Payroll", "Admin", "Change", "Secure", "Office", "Test", "Temp", "Qwerty", "Sunrise"))
        sym = rng.choice("@#$!%&*")
        tail = rng.choice((_digits(rng, 3), _digits(rng, 4), str(rng.randint(2019, 2026))))
        return rng.choice((f"{word}{sym}{tail}", f"{word.lower()}{tail}{sym}", f"{word}{tail}{sym}{_upper(rng, 1)}",
                           "".join(rng.choice(a62 + "!@#$%^&*") for _ in range(rng.randint(10, 16)))))
    raise ValueError(f"unknown secret kind {kind!r}")


# ------------------------------------------------------------------------------------------ OCR-style noise

_OCR_SUB = {"O": "0", "0": "O", "l": "1", "1": "l", "I": "l", "S": "5", "5": "S", "B": "8", "8": "B", "rn": "m", "m": "rn"}


def ocr_noise(rng: random.Random, s: str, rate: float = 0.04) -> str:
    """Character-level OCR confusions on literal text (never inserts newlines)."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        r = rng.random()
        if r < rate and c in _OCR_SUB:
            out.append(_OCR_SUB[c])
        elif r < rate * 1.4 and c == " ":
            out.append("  ")
        elif r < rate * 1.6 and c.isalpha():
            pass  # dropped character
        else:
            out.append(c)
        i += 1
    return "".join(out)


# ================================================================================================ HARD NEGATIVES
# Values that look like PII (or collide with name lists) and must NOT be labelled. Each returns plain text.

PLACES = ("Vijayawada", "Tiruchirappalli", "Thiruvananthapuram", "Bengaluru", "Visakhapatnam", "Kozhikode", "Mangaluru", "Nagpur",
          "Coimbatore", "Guwahati", "Bhubaneswar", "Jamshedpur", "Ahmedabad", "Chandigarh", "Pune", "Hyderabad", "Kolkata",
          "Lucknow", "Indore", "Madurai", "Mysuru", "Shillong", "Dehradun", "Ranchi", "Raipur", "Surat", "Vadodara", "Kochi",
          "Gandhinagar", "Rajkot", "Salem", "Tirupati", "Warangal", "Ludhiana", "Amritsar", "Siliguri", "Durgapur", "Panaji")
NAMED_PLACES = ("Mahatma Gandhi Road", "Dr. Ambedkar Nagar", "Nehru Place", "Sardar Patel Marg", "Rajaji Nagar", "Jayanagar 4th Block",
                "Lal Bahadur Shastri Marg", "Subhash Chowk", "Tagore Lane", "Ramesh Nagar", "Lakshmi Bai Colony", "Sarojini Nagar",
                "Kasturba Gandhi Road", "Anna Salai", "Bose Road", "Kamala Nehru Park", "Vivekananda Street", "Sunita Vihar")
ORGS_NAMELIKE = ("Ramesh Textiles Pvt Ltd", "Sharma & Sons", "Kiran Enterprises", "Lakshmi Narayan Trust", "Gupta Brothers Logistics",
                 "Anita Foods", "Venkatesh Auto Works", "Patel Steel Corporation", "Mohammed Ali Traders", "Fernandes Marine Services",
                 "Sunita Devi Memorial School", "Rahul Packaging Industries", "Singh Transport Co.", "Iyer Associates LLP",
                 "Reddy Labs", "Kapoor and Company", "Joseph Mathew Foundation", "Gandhi Hospital", "Tata Memorial Wing")
NAME_WORDS = ("ASHA worker", "asha (hope)", "Kiran (ray of light)", "Pooja room", "Diwali puja", "Amrit Kaal", "Ganga water",
              "Sagar Mala project", "the Kamal symbol", "Jyoti Nagar office", "Mala (garland)", "Deepak (lamp)", "Chandni Chowk",
              "Surya Namaskar session", "Bharti Airtel recharge", "Raja Yoga class", "Rani Bagh", "Shanti Bhavan guest house")
ROLE_MAIL_LOCAL = ("hr", "payroll", "helpdesk", "hrhelpdesk", "recruitment", "benefits", "admin", "it.support", "careers", "accounts")


def neg_ticket(rng: random.Random) -> str:
    return rng.choice((f"#{rng.randint(1000, 999999)}", f"ticket {rng.randint(100, 99999)}", f"TKT-{rng.randint(100, 999)}",
                       f"case no. {rng.randint(10, 9999)}", f"SR {rng.randint(10000, 99999)}", f"ref {rng.randint(1, 999)}"))


def neg_statutory(rng: random.Random) -> str:
    amt = rng.choice((("Section 80C", "1,50,000"), ("Section 80D", "25,000"), ("Section 80CCD(1B)", "50,000"),
                      ("standard deduction", "75,000"), ("PF wage ceiling", "15,000"), ("ESI wage limit", "21,000"),
                      ("gratuity cap", "20,00,000"), ("basic exemption limit", "3,00,000"), ("Section 87A rebate limit", "7,00,000"),
                      ("professional tax slab", "25,000"), ("bonus eligibility ceiling", "21,000")))
    sym = rng.choice(("Rs. ", "₹", "INR ", "Rs "))
    return f"{amt[0]} limit of {sym}{amt[1]}"


def neg_invoice(rng: random.Random) -> str:
    amt = rng.randint(1200, 9_999_999)
    return f"invoice total {rng.choice(('Rs. ', '₹', 'INR '))}{indian_group(amt)}.{rng.choice(('00', '50', '75'))}"


def neg_extension(rng: random.Random) -> str:
    return rng.choice((f"ext. {rng.randint(100, 9999)}", f"x{rng.randint(1000, 9999)}", f"extension {rng.randint(100, 999)}",
                       f"desk {rng.randint(1, 9)}-{rng.randint(100, 999)}", f"floor {rng.randint(1, 20)} bay {rng.randint(1, 99)}"))


def neg_helpline(rng: random.Random) -> str:
    return rng.choice((f"1800-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}", f"1800 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
                       "112", "100", "1098", "14567", "1860-500-1234", "155620"))


def neg_date(rng: random.Random) -> str:
    return rng.choice((f"FY {rng.randint(2018, 2025)}-{rng.randint(19, 26)}", f"AY {rng.randint(2019, 2026)}-{rng.randint(20, 27)}",
                       "Q2 FY25", "March 2024", "week 14", "payslip for " + rng.choice(("April", "May", "October")) + f" {rng.randint(2021, 2026)}",
                       "effective " + other_date(rng), "date of joining " + other_date(rng), "last working day " + other_date(rng),
                       "deadline " + other_date(rng), "paid on " + other_date(rng)))


def neg_pin(rng: random.Random) -> str:
    return rng.choice(("PIN ", "Pincode ", "PIN code: ", "")) + str(rng.randint(110001, 855117))


def neg_count(rng: random.Random) -> str:
    return rng.choice((f"{rng.randint(12, 4800)} employees", f"{rng.randint(5, 99)}% hike", f"{rng.randint(1, 40)} days of leave",
                       f"{rng.randint(20, 48)} hours", f"{rng.randint(1, 12)} years", f"version {rng.randint(1, 9)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}",
                       f"grade L{rng.randint(1, 9)}", f"band B{rng.randint(1, 6)}", f"cost centre {rng.randint(1000, 9999)}",
                       f"dept code {rng.randint(10, 99)}-{rng.randint(100, 999)}"))


def neg_role_email(rng: random.Random) -> str:
    _, stem = company(rng)
    return f"{rng.choice(ROLE_MAIL_LOCAL)}@{stem}.{rng.choice(('com', 'in', 'co.in'))}"


def neg_place(rng: random.Random) -> str:
    return rng.choice(PLACES)


def neg_namedplace(rng: random.Random) -> str:
    return rng.choice(NAMED_PLACES)


def neg_org(rng: random.Random) -> str:
    return rng.choice(ORGS_NAMELIKE)


def neg_nameword(rng: random.Random) -> str:
    return rng.choice(NAME_WORDS)


NEGATIVE_GENERATORS = {
    "TICKET": neg_ticket, "STATUTORY": neg_statutory, "INVOICE": neg_invoice, "EXT": neg_extension, "HELPLINE": neg_helpline,
    "DATE": neg_date, "PIN": neg_pin, "COUNT": neg_count, "ROLE_EMAIL": neg_role_email, "PLACE": neg_place,
    "NAMEDPLACE": neg_namedplace, "ORG": neg_org, "NAMEWORD": neg_nameword,
}

# Names/words that the unslotted-name check in synth.validate_template must not treat as a leaked person name.
AMBIGUOUS_WORDS: frozenset[str] = frozenset(
    w for phrase in NAME_WORDS + ORGS_NAMELIKE + NAMED_PLACES for w in phrase.replace("(", " ").replace(")", " ").split())
