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
