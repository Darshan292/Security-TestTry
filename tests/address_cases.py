"""Deterministic home-address cases WITHOUT an ML detector (the structure of what an external tester sent: street,
locality and landmark pieces with a PIN, in one line, over several lines, inside prose), and benign text with PINs and
six-digit numbers. Written for pgw.detect.places `pin_block`; the vocabulary here is test data, not detector code.
"""
from __future__ import annotations

import random

HOUSE = ["12/B", "No. 45", "#7-3-112", "Flat 302", "H.No. 8-2-120", "23", "Plot 44", "B-17", "Door No 5-9-112", "301"]
BUILDING = ["Sai Krupa Apartments", "Lakeview Residency", "Shanti Nivas", "Green Meadows", "Ganga Heights", "Rose Villa",
            "Anand Bhavan", "Skyline Towers"]
STREET = ["4th Cross", "MG Road", "Temple Street", "Gandhi Marg", "Ring Road", "Nehru Lane", "Church Road", "2nd Main"]
LOCALITY = ["Koramangala 4th Block", "Malleswaram", "Jayanagar", "Banjara Hills", "Kothrud", "Salt Lake", "Anna Nagar",
            "Indiranagar", "Civil Lines", "Sector 21", "Vasant Vihar", "Gachibowli", "Adyar", "Powai"]
LANDMARK = ["Near Hanuman Temple", "Opp. City Mall", "Behind SBI ATM", "Beside Apollo Pharmacy", "Near Metro Station",
            "Opposite Government School"]
CITY = [("Bengaluru", "560034"), ("Hyderabad", "500034"), ("Pune", "411038"), ("Kolkata", "700091"), ("Chennai", "600020"),
        ("Mumbai", "400076"), ("Nagpur", "440013"), ("Lucknow", "226001"), ("Jaipur", "302017"), ("Delhi", "110017")]
LEAD = ["My address is ", "I live at ", "Please courier it to ", "Address: ", "Residence - ", "Permanent address: ",
        "Send the letter to Ravi Kumar, ", ""]
TRAIL = [" Please confirm.", " Thanks.", "", " Call me before delivery.", ""]
CUE_FORMS = ("{lead}{body}.{trail}", "{lead}{body}{trail}")


def _pick(r, xs, k):
    return r.sample(xs, k)


def home_addresses(n: int = 120, seed: int = 7) -> list[dict]:
    """[{text, parts}]: `parts` are the street/locality/landmark pieces that must be masked (city and state names are
    general geography and stay outside the span by design)."""
    r = random.Random(seed)
    out = []
    for i in range(n):
        city, pin = r.choice(CITY)
        kind = i % 8
        if kind == 0:     # house-less, comma separated, landmark first
            parts = [r.choice(LANDMARK) + " " + r.choice(BUILDING), r.choice(LOCALITY)]
            sep, tail = ", ", f", {city} - {pin}"
        elif kind == 1:   # locality + street, no house number
            parts = [r.choice(STREET), r.choice(LOCALITY)]
            sep, tail = ", ", f", {city} {pin}"
        elif kind == 2:   # bare building and locality
            parts = [r.choice(BUILDING), r.choice(LOCALITY), r.choice(LANDMARK)]
            sep, tail = ", ", f", {city}-{pin}"
        elif kind == 3:   # house + street
            parts = [r.choice(HOUSE) + " " + r.choice(STREET), r.choice(LOCALITY)]
            sep, tail = ", ", f", {city} PIN {pin}"
        elif kind == 4:   # multi-line, no commas
            parts = [r.choice(BUILDING), r.choice(STREET), r.choice(LOCALITY)]
            sep, tail = "\n", f"\n{city} - {pin}"
        elif kind == 6:   # casual lower-case writing, comma separated
            parts = [r.choice(LANDMARK).lower() + " " + r.choice(BUILDING).lower(), r.choice(LOCALITY).lower()]
            sep, tail = ", ", f", {city.lower()} {pin}"
        elif kind == 7:   # one run without commas, in prose
            parts = [r.choice(STREET).lower(), r.choice(LOCALITY).lower()]
            sep, tail = " ", f" {city.lower()} {pin}"
        else:             # multi-line with commas, landmark line
            parts = [r.choice(LANDMARK), r.choice(STREET), r.choice(LOCALITY)]
            sep, tail = ",\n", f",\n{city} {pin}"
        body = sep.join(parts) + tail
        if "\n" in sep:
            text = f"Employee: Meera Iyer\nContact: 98450 12345\nAddress:\n{body}\nJoining date: 01-04-2024\n"
        else:
            lead = r.choice(LEAD)
            text = lead + body + ("." if lead and r.random() < 0.5 else "") + r.choice(TRAIL)
        out.append({"text": text, "parts": parts, "pin": pin})
    return out


BENIGN = [
    "The office in Bengaluru 560034 is closed on Sunday. Invoice no 560034 was raised.",
    "Please quote reference 123456 in your reply, thanks.",
    "Salary credited: 450000 on 05-04-2024. Bonus 120000, Total 570000.",
    "Our registered office is at Tower B, Prestige Tech Park, Bengaluru 560103. For queries write to hr@acme.example.",
    "Call the helpline 1800 123 456 or visit the Mumbai 400001 branch. The pin code of the branch is 400001.",
    "Dear Team, the training will be held in Pune 411001. Please be on time. Regards, Admin",
    "Ticket 345678 moved to Resolved. Cost centre CC 789012 charged. Pincode 600001 appears in the form header.",
    "The court is in Delhi 110001 and the hearing is on 12 March. Meera Iyer will attend.",
    "Amount payable Rs. 560034 only. Remarks: Chennai 600001 office, approved by Rao.",
    "Policy FAQ: ZIP / PIN codes must be six digits, for example Hyderabad - 500001. Do not abbreviate the city.",
    "Quarterly review\nStatus: On track\nOwner: Operations, Pune 411038\nNext review: 15 July\n",
    "Dispatch to the Kolkata 700091 warehouse completed. Weight 120000 grams, Batch 654321.",
]


# --- separator, layout and case variants (round 9: pipes, slashes, semicolons, tabs, a PIN on its own line, lower case,
# --- no PIN). Same rule as above: the pieces are test data, the detector has no street-name list.
HOUSE_MARKED = ["Flat 302", "H.No. 8-2-120", "House No. 17", "Plot 44", "#45", "Door No 5-9-112", "Flat 12B", "H.No. 1123"]
SEPARATORS = {"pipe": " | ", "slash": " / ", "semi": "; ", "tab": "\t", "dash": " - "}
VARIANT_CATEGORIES = ("pipe", "slash", "semi", "tab", "dash", "pin_own_line", "lower_comma", "lower_prose",
                      "nopin_house", "nopin_nohouse")
# categories no structural rule can fully cover: no PIN and no house marker, or lower-case prose (documented residuals)
RESIDUAL_CATEGORIES = ("nopin_nohouse", "lower_prose")
_LOWER_TAIL = [" is correct", " asap please", " for the new joiner", " before friday", ""]
_NOUN_LEAD = ["my new address is ", "please courier it to ", "i moved from ", "the address ", "send the offer letter to "]


def variant_cases(n_per: int = 16, seed: int = 11) -> list[dict]:
    """[{cat, text, parts, pin, keep}]: `parts` and `pin` must be masked; `keep` are ordinary words that must stay."""
    r = random.Random(seed)
    out: list[dict] = []
    for cat in VARIANT_CATEGORIES:
        for _ in range(n_per):
            city, pin = r.choice(CITY)
            house = r.choice(HOUSE_MARKED)
            street = r.choice(STREET)
            loc = r.choice(LOCALITY)
            bld = r.choice(BUILDING)
            keep: list[str] = []
            if cat in SEPARATORS:
                sep = SEPARATORS[cat]
                parts = [f"{house} {street}", bld, loc]
                body = sep.join(parts + [city, pin])
                if cat == "tab":
                    text = f"Meera Iyer\t{body}" if r.random() < 0.5 else f"Address:\t{body}"
                else:
                    text = r.choice(["Address: ", "Addr: ", "Ship to ", "Residence - "]) + body
                    if r.random() < 0.5:
                        text += ". Please confirm."
                        keep = ["Please confirm"]
            elif cat == "pin_own_line":
                parts = [f"{house} {street}", loc] if r.random() < 0.5 else [bld, street, loc]
                comma = r.choice([",", ""])
                text = ("Employee: Meera Iyer\nAddress:\n" + (comma + "\n").join(parts) + comma + f"\n{city}\n{pin}\n"
                        "Joining date: 01-04-2024\n")
                keep = ["Joining date"]
            elif cat == "lower_comma":
                parts = [f"{house} {street}".lower(), loc.lower()]
                tail = r.choice(_LOWER_TAIL)
                text = r.choice(["my address is ", "ship to ", "address: "]) + ", ".join(parts) + \
                    f", {city.lower()} {pin}" + tail
                keep = [tail.strip()] if tail.strip() else []
            elif cat == "lower_prose":
                parts = []                       # lower-case prose: only the PIN is required, the words must stay
                lead, tail = r.choice(_NOUN_LEAD), r.choice(_LOWER_TAIL[:-1])
                text = lead + f"{street} {loc} {city} {pin}".lower() + tail
                keep = [lead.strip().split()[0], tail.strip().split()[0]]
            elif cat == "nopin_house":
                parts = [f"{house} {street}", loc]
                text = r.choice(["Address: ", "Residence - ", "lives at ", ""]) + ", ".join(parts + [city]) + \
                    r.choice([".", ""])
                pin = ""
            else:                                # nopin_nohouse: nothing structural to hold on to
                parts = [bld, loc]
                text = "Staying at " + ", ".join(parts + [city, r.choice(["Kerala", "Goa", "Assam"])])
                pin = ""
            out.append({"cat": cat, "text": text, "parts": parts, "pin": pin, "keep": keep})
    return out


# Tab-separated rows led by a name the pipeline already protects (the round-9 regression): the name must not swallow the
# address block, and the address must survive next to it.
TAB_ROWS = [("Ritika Banerjee", "H.No. 1123, Sector 15-C", "Chandigarh", "160015"),
            ("Ritika Banerjee", "Flat 4B, Rose Villa, Adyar", "Chennai", "600020"),
            ("Ritika", "Plot 44, Gandhi Marg, Civil Lines", "Jaipur", "302017")]
