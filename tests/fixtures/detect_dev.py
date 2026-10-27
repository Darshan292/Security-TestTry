"""Development set for the detection classes (written for this work; every person and value is invented).

Each case: id, cls (1-8, see tests/test_detection_classes.py), text, `leak` (strings that must not reach the
provider), `keep` (strings that must cross unchanged), optional `declared` (contract-declared person names) and
`roster` (True: the dev roster below is loaded). Negative cases have an empty `leak` list.

This set is used for measuring during development only. The tests in tests/test_detection_classes.py use other
values.
"""
from __future__ import annotations


def iban(country: str, bban: str) -> str:
    """A mod-97 valid IBAN for an invented BBAN (ISO 13616 check digits)."""
    s = bban + country + "00"
    n = int("".join(str(int(c, 36)) for c in s))
    return f"{country}{98 - n % 97:02d}{bban}"


def group4(s: str, sep: str = " ") -> str:
    return sep.join(s[i:i + 4] for i in range(0, len(s), 4))


ROSTER = [
    {"employee_id": "QX-30418", "full_name": "Devraj Hegde", "first_name": "Devraj", "last_name": "Hegde",
     "email": "devraj.hegde@lumora.in", "phone": "+91 99016 23874"},
    {"employee_id": "QX-30422", "full_name": "Ishaan Kohli", "first_name": "Ishaan", "last_name": "Kohli",
     "email": "ishaan.kohli@lumora.in", "phone": "+91 98860 41275"},
    {"employee_id": "QX-30577", "full_name": "Pranjal Borkar", "first_name": "Pranjal", "last_name": "Borkar",
     "email": "pranjal.borkar@lumora.in", "phone": "+91 97311 08462"},
    {"employee_id": "QX-30591", "full_name": "Yamini Sabnis", "first_name": "Yamini", "last_name": "Sabnis",
     "email": "yamini.sabnis@lumora.in", "phone": "+91 90080 55217"},
    {"employee_id": "QX-30602", "full_name": "Tushar Wagle", "first_name": "Tushar", "last_name": "Wagle",
     "email": "tushar.wagle@lumora.in", "phone": "+91 96320 71843"},
]

I1 = iban("DE", "370400440532013000")
I2 = iban("GB", "NWBK60161331926819")
I3 = iban("FR", "20041010050500013M02606")
I4 = iban("AE", "0331234567890123456")
I5 = iban("SA", "80000000608010167519")
I6 = iban("NL", "ABNA0417164300")
I7 = iban("CH", "00762011623852957")
I8 = iban("QA", "DOHB00001234567890ABCDEFG")

C = []


def case(cid, cls, text, leak=(), keep=(), declared=(), roster=False):
    C.append({"id": cid, "cls": cls, "text": text, "leak": list(leak), "keep": list(keep),
              "declared": list(declared), "roster": roster})


# --- 1. name words adjacent to a detected or declared name part ------------------------------------------------
case("1.01", 1, "Please loop in Shalini Phadnis from the Pune office regarding the transfer.", ["Shalini"],
     ["Pune office"], declared=["Harshad Phadnis"])
case("1.02", 1, "Tanmay's wife Revati Bhide has been added as the nominee.", ["Revati", "Bhide"],
     ["nominee"], declared=["Tanmay Bhide"])
case("1.03", 1, "Regards,\nGayatri\nVenkataraman\nPayroll Desk", ["Gayatri", "Venkataraman"], ["Payroll Desk"])
case("1.04", 1, "Ishaan Malhotra asked about his travel reimbursement.", ["Ishaan", "Malhotra"],
     ["travel reimbursement"], roster=True)
case("1.05", 1, "Nominee: Sushila Devi w/o Baldev Raj Chauhan, aged 52.", ["Baldev", "Chauhan", "Sushila"])
case("1.06", 1, "Applicant Farhan Qureshi s/o Late Anwar Qureshi, resident of Lucknow.", ["Anwar", "Farhan"])
case("1.07", 1, "Form 11 was filed by Mrs. Lalitha Krishnan d/o Shri R. Gopalakrishnan.",
     ["Lalitha", "Gopalakrishnan"], ["Form 11"])
case("1.08", 1, "The nominee is the son of Mr. Devendra Prasad Sinha.", ["Devendra", "Sinha"])
case("1.09", 1, "The claim was raised by Rukmini, daughter of late Shri Venkat Rao Naidu.", ["Venkat", "Naidu"])
case("1.10", 1, "Prof. Dr. Anirban Chakraborty will chair the review panel.", ["Anirban", "Chakraborty"],
     ["review panel"])
case("1.11", 1, "Dr (Mrs) Hemlata Joglekar has approved the leave.", ["Hemlata", "Joglekar"], ["approved the leave"])
case("1.12", 1, "Late Smt. Kamala Thakur's pension papers are pending.", ["Kamala", "Thakur"], ["pension papers"])
case("1.13", 1, "Lt. Col. Prakash Rawat (retd) joined as security head.", ["Prakash", "Rawat"], ["security head"])
case("1.14", 1, "Send the kit c/o Mohinder Pal Bajwa at the guest house.", ["Mohinder", "Bajwa"], ["guest house"])
case("1.15", 1, "Gratuity nominee: Rekha Dalvi s/o ramprasad yadav", ["ramprasad", "yadav"])
case("1.16", 1, "Please forward the letter to Abhay Ramchandra Deshmukh today.", ["Ramchandra", "Abhay"],
     ["forward the letter"], declared=["Abhay Deshmukh"])
case("1.17", 1, "Husband of Smt. Jyotsna Barve, Shri Nitin Barve, is the guarantor.", ["Jyotsna", "Nitin"])
case("1.18", 1, "Wife of late Kishore Lal Mathur has applied for the family pension.", ["Kishore", "Mathur"],
     ["family pension"])
case("1.19", 1, "Kindly ask Pranjal Ghatge to resend the payslip.", ["Ghatge", "Pranjal"], ["resend the payslip"],
     roster=True)
case("1.20", 1, "Thanks,\nTushar\nNaik", ["Naik", "Tushar"], [], roster=True)
case("1.21", 1, "Sri Sri Anantharaman Iyengar has retired.", ["Anantharaman", "Iyengar"], ["has retired"])
case("1.22", 1, "Adv. Meenakshi Sundaram represented the employee.", ["Meenakshi", "Sundaram"])
# negatives: capitalised ordinary words next to a name stay
case("1.n1", 1, "Kiran Bhatt Joined the Finance Team on Monday.", [], ["Joined", "Finance Team", "Monday"],
     declared=["Kiran Bhatt"])
case("1.n2", 1, "Yesterday Sameer submitted the form.", [], ["Yesterday", "submitted the form"],
     declared=["Sameer Gokhale"])
case("1.n3", 1, "Approved by Leena Fernandes Head of Payroll Operations.", [], ["Head of Payroll Operations"],
     declared=["Leena Fernandes"])
case("1.n4", 1, "Subject: Ajay Kulkarni Offer Letter Draft", [], ["Offer Letter Draft"], declared=["Ajay Kulkarni"])
case("1.n5", 1, "The S/O field in Form 2 must be filled in capitals.", [], ["S/O field in Form 2"])
case("1.n6", 1, "He arrived Late Monday Evening after the offsite.", [], ["Late Monday Evening"])
case("1.n7", 1, "The Late Coming Policy applies from April.", [], ["Late Coming Policy"])
case("1.n8", 1, "Please Review And Approve the attached timesheet.", [], ["Please Review And Approve"])
case("1.n9", 1, "Devraj Will Share The Deck Tomorrow", [], ["Will Share The Deck Tomorrow"], roster=True)

# --- 2. names in tables and CSV/TSV pastes ------------------------------------------------------------------------
case("2.01", 2, "Emp Name,Dept,Location\nTarun Bhatia,Finance,Pune\nOjaswini Rathore,Legal,Jaipur\n"
     "Madhav Iyengar,IT,Chennai", ["Tarun", "Ojaswini", "Rathore", "Madhav", "Iyengar", "Bhatia"],
     ["Finance", "Legal", "Jaipur", "Chennai"])
case("2.02", 2, "Employee\tGrade\tCity\nNandita Purohit\tL4\tIndore\nSaket Bhardwaj\tL5\tNoida",
     ["Nandita", "Purohit", "Saket", "Bhardwaj"], ["Indore", "Noida"])
case("2.03", 2, "| Manager | Team | Headcount |\n|---|---|---|\n| Raghav Chitnis | Payments | 14 |\n"
     "| Ilakkiya Selvam | Lending | 9 |", ["Raghav", "Chitnis", "Ilakkiya", "Selvam"], ["Payments", "Lending"])
case("2.04", 2, "Reportee | Rating | Increment\nVaidehi Kale | 4 | 9%\nSoham Mahajan | 3 | 6%",
     ["Vaidehi", "Kale", "Soham", "Mahajan"], ["Rating"])
case("2.05", 2, "Name;Emp Code;Branch\nGautami Bhonsle;QX-11;Nashik\nHrishikesh Oak;QX-12;Satara",
     ["Gautami", "Bhonsle", "Hrishikesh", "Oak"], ["Nashik", "Satara"])
case("2.06", 2, "Full Name,Status\nanupama roy,Active\nbikram sarkar,On leave",
     ["anupama", "bikram", "sarkar"], ["Active", "On leave"])
case("2.07", 2, "Nominee Name,Relation,Share\nSavitri Pande,Mother,50\nOmkar Pande,Brother,50",
     ["Savitri", "Omkar", "Pande"], ["Mother", "Brother"])
case("2.08", 2, "Father's Name\tDOB\nRamakant Joshi\t12/03/1961\nShivnath Tripathi\t05/11/1958",
     ["Ramakant", "Shivnath", "Tripathi"])
case("2.09", 2, "Devraj Hegde,Bengaluru,Analyst\nYamini Sabnis,Mumbai,Lead\nTushar Wagle,Pune,Manager\n"
     "Chaitali Mhatre,Thane,Analyst", ["Chaitali", "Mhatre"], ["Bengaluru", "Analyst"], roster=True)
case("2.10", 2, "Approver,Request,Days\nKunal Dhage,Casual leave,2\nSmita Rege,Sick leave,1",
     ["Kunal", "Dhage", "Smita", "Rege"], ["Casual leave", "Sick leave"])
case("2.11", 2, "Interviewer\tSlot\nAkshata Pai\t10:00\nVineet Sood\t11:30",
     ["Akshata", "Vineet", "Sood"], ["10:00", "11:30"])
case("2.12", 2, '"Employee Name","Bank"\n"Mrinal Jagtap","Canara Bank"\n"Shreyas Bapat","Axis Bank"',
     ["Mrinal", "Jagtap", "Shreyas", "Bapat"], ["Canara Bank", "Axis Bank"])
case("2.n1", 2, "Department Name,Head Count\nFinance,42\nTreasury,7\nProcurement,19", [],
     ["Finance", "Treasury", "Procurement"])
case("2.n2", 2, "Project Name,Status\nApollo Migration,Done\nLedger Rewrite,Open", [],
     ["Apollo Migration", "Ledger Rewrite"])
case("2.n3", 2, "Bank Name\tIFSC Prefix\nCanara Bank\tCNRB\nIndian Bank\tIDIB", [], ["Canara Bank", "Indian Bank"])
case("2.n4", 2, "| Policy | Owner Team | Review |\n|---|---|---|\n| Leave Policy | People Ops | Annual |\n"
     "| Travel Policy | Finance | Biennial |", [], ["People Ops", "Finance", "Leave Policy"])
case("2.n5", 2, "Designation,Band\nSenior Analyst,B2\nAssociate Director,D1", [], ["Senior Analyst", "Associate Director"])

# --- 3. lowercase names ---------------------------------------------------------------------------------------------
case("3.01", 3, "pls ask devraj to share the Form 16 by Friday.", ["devraj"], ["Form 16"], roster=True)
case("3.02", 3, "charulata sent the documents yesterday.", ["charulata"], ["documents"], declared=["Charulata Sen"])
case("3.03", 3, "Dear Mr. Omkar Pawar, we have reviewed omkar pawar's appeal.", ["omkar", "pawar", "Omkar"])
case("3.04", 3, "Can you check why venkatesh raghavan's reimbursement is pending?", ["venkatesh", "raghavan"],
     ["reimbursement is pending"])
case("3.05", 3, "Thanks for the update.\nregards,\nsneha kulkarni", ["sneha", "kulkarni"], ["Thanks for the update"])
case("3.06", 3, "hi tejas, My Form 16 for last year is missing.", ["tejas"], ["Form 16"])
case("3.07", 3, "As discussed, mr. bharadwaj approved the exception.", ["bharadwaj"], ["approved the exception"])
case("3.08", 3, "Please ping anirudh or kshitij about the payslip.", ["anirudh", "kshitij"], ["payslip"])
case("3.09", 3, "I spoke to rohini mukund in Payroll about the arrears.", ["rohini", "mukund"], ["Payroll"])
case("3.10", 3, "My manager is jayant pradhan and he has not approved it.", ["jayant", "pradhan"])
case("3.11", 3, "Please check with yamini, she handles the BGV.", ["yamini"], ["BGV"], roster=True)
case("3.12", 3, "Dear tanushree,\nYour leave is approved.", ["tanushree"], ["Your leave is approved"])
case("3.n1", 3, "hi team, pls check the HRMS login issue.", [], ["hi team, pls check the HRMS login issue."])
case("3.n2", 3, "hi bhai, salary kab aayegi?", [], ["hi bhai, salary kab aayegi?"])
case("3.n3", 3, "Please ask payroll to rerun the batch.", [], ["Please ask payroll to rerun the batch."])
case("3.n4", 3, "I spoke to finance about the reimbursement.", [], ["I spoke to finance about the reimbursement."])
case("3.n5", 3, "regards,\nhr ops", [], ["regards,\nhr ops"])
case("3.n6", 3, "Hello sir, kindly help with the PF transfer.", [], ["Hello sir, kindly help"])
case("3.n7", 3, "Talk to someone in admin about the access card.", [], ["Talk to someone in admin"])
case("3.n8", 3, "Thanks all, the issue is resolved now.", [], ["Thanks all, the issue is resolved now."])
case("3.n9", 3, "dear sir/madam, the portal shows an error on submit.", [], ["dear sir/madam"])
case("3.n10", 3, "Ping jira support with the ticket link.", [], ["Ping jira support"])

# --- 4. IBAN ------------------------------------------------------------------------------------------------------
case("4.01", 4, f"Please pay the relocation grant to IBAN {I1}.", [I1, I1[4:]])
case("4.02", 4, f"Her German account: {group4(I1)} (Commerzbank).", [group4(I1)], ["Commerzbank"])
case("4.03", 4, f"UK salary account {group4(I2)} for the secondment.", [group4(I2)], ["secondment"])
case("4.04", 4, f"Refund to {group4(I3)} please.", [group4(I3)])
case("4.05", 4, f"IBAN: {I4[:2]} {I4[2:4]} {I4[4:7]} {I4[7:13]} {I4[13:]}", [I4[13:]])
case("4.06", 4, f"Transfer the end-of-service dues to {group4(I5, '-')}", [group4(I5, '-')])
case("4.07", 4, f"account {I6.lower()} at the Dutch bank", [I6.lower()])
case("4.08", 4, f"Swiss account {group4(I7)} needs updating", [group4(I7)])
case("4.09", 4, f"Qatar payroll IBAN {group4(I8)}", [group4(I8)])
case("4.n1", 4, "ISO 9001 and IS 456 compliance is mandatory for vendors.", [], ["ISO 9001", "IS 456"])
case("4.n2", 4, "Ticket DE12 is about the German onboarding flow.", [], ["DE12"])
case("4.n3", 4, "Use the GB format on page 22 of the manual.", [], ["GB format on page 22"])

# --- 5. addresses ---------------------------------------------------------------------------------------------------
case("5.01", 5, "Current address: Flat 302, Sai Krupa Apartments, 4th Cross, Malleswaram, Bengaluru 560003",
     ["302", "Sai Krupa Apartments", "4th Cross", "560003"], ["Current address"])
case("5.02", 5, "Please courier the letter to House No. 17, Gandhi Nagar, Nagpur - 440013.",
     ["House No. 17", "440013"], ["courier the letter"])
case("5.03", 5, "She lives at 23/B, Shanti Vihar, Sector 9, Rohini, Delhi 110085", ["23/B", "Shanti Vihar", "110085"])
case("5.04", 5, "New address is Plot 44, Mahatma Phule Road, Kothrud, Pune, Maharashtra 411038.",
     ["Plot 44", "Mahatma Phule Road", "411038"])
case("5.05", 5, "PIN: 682018, Ernakulam", ["682018"])
case("5.06", 5, "Pincode 500 081 for the Hyderabad residence", ["500 081"])
case("5.07", 5, "Door No 5-9-112, Lakdikapul, Hyderabad, Telangana 500004", ["5-9-112", "500004"])
case("5.08", 5, "Permanent address: 12, Ramanuja Street, T. Nagar, Chennai 600017", ["12, Ramanuja Street", "600017"])
case("5.09", 5, "Ship it to #45, 2nd Main Road, Indiranagar, Bangalore-560038", ["#45", "2nd Main Road", "560038"])
case("5.10", 5, "Residence: B-704, Lake View Towers, Powai, Mumbai 400076", ["B-704", "Lake View Towers", "400076"])
case("5.n1", 5, "The interview is in Room 204 on the 3rd floor.", [], ["Room 204 on the 3rd floor"])
case("5.n2", 5, "Build 560003 failed on the payroll service.", [], ["Build 560003 failed"])
case("5.n3", 5, "The Bengaluru office has 450 people.", [], ["Bengaluru office has 450 people"])
case("5.n4", 5, "The pincode field is mandatory on the form.", [], ["pincode field is mandatory"])
case("5.n5", 5, "Order 400076 was shipped in Mumbai yesterday.", [], ["Order 400076"])
case("5.n6", 5, "Sector 5 teams meet on Road Safety Day.", [], ["Sector 5 teams meet on Road Safety Day"])

# --- 6. amounts with no cue word --------------------------------------------------------------------------------
case("6.01", 6, "This month only 48,500/- got credited to my account.", ["48,500"], ["credited to my account"])
case("6.02", 6, "I received 62,300 rs after deductions.", ["62,300"], ["after deductions"])
case("6.03", 6, "He was offered 2.4 million rupees by the competitor.", ["2.4 million"], ["competitor"])
case("6.04", 6, "My in-hand is 71,250 after tax.", ["71,250"])
case("6.05", 6, "They deducted 3,450 INR from my settlement.", ["3,450"], ["settlement"])
case("6.06", 6, "Her retention payout of INR 1.5 million is due.", ["1.5 million"])
case("6.07", 6, "I got only 39,800/- this month instead of the usual.", ["39,800"])
case("6.08", 6, "Rs 54,000 got credited to her account on the 1st.", ["54,000"])
case("6.09", 6, "his take home was 82,400 rupees", ["82,400"])
case("6.10", 6, "I was paid 28,750 for March, which is too low.", ["28,750"])
case("6.n1", 6, "The laptop costs 65,000/- including GST.", [], ["65,000/-"])
case("6.n2", 6, "We have 45,000 employees across 12 cities.", [], ["45,000 employees"])
case("6.n3", 6, "The vendor invoice is 4,200 rs for the team lunch.", [], ["4,200 rs"])
case("6.n4", 6, "Revenue crossed 2 billion rupees this year.", [], ["2 billion rupees"])
case("6.n5", 6, "The maximum deduction under 80C is 1,50,000 per year.", [], ["1,50,000"])
case("6.n6", 6, "The portal handled 12,500 requests yesterday.", [], ["12,500 requests"])

# --- 7. short-form dates of birth ---------------------------------------------------------------------------------
case("7.01", 7, "DOB: 23rd Jul '91", ["23rd Jul '91"])
case("7.02", 7, "born on 14-Aug-89 in Kolhapur", ["14-Aug-89"], ["Kolhapur"])
case("7.03", 7, "date of birth jul 23 1991", ["jul 23 1991"])
case("7.04", 7, "She was born on the 5th of March, 1988.", ["5th of March, 1988"])
case("7.05", 7, "Birthday: twenty-third of July 1991", ["twenty-third of July 1991"])
case("7.06", 7, "D.O.B. 9 Sept '93", ["9 Sept '93"])
case("7.07", 7, "dob 02-Feb-2001", ["02-Feb-2001"])
case("7.08", 7, "my date of birth is March 3rd, '95", ["March 3rd, '95"])
case("7.09", 7, "born 1st Jan'90", ["1st Jan'90"])
case("7.10", 7, "Date of Birth - Thirty First December 1979", ["Thirty First December 1979"])
case("7.n1", 7, "Joining date 23-Jul-21 for the new hire.", [], ["23-Jul-21"])
case("7.n2", 7, "The meeting is on the 5th of March at noon.", [], ["5th of March"])
case("7.n3", 7, "Release 3rd Jul '24 notes are attached.", [], ["3rd Jul '24"])

# --- 8. letter-for-digit swaps --------------------------------------------------------------------------------------
case("8.01", 8, "my aadhaar is 2O34 5l78 9O12, please update", ["2O34 5l78 9O12"], ["please update"])
case("8.02", 8, "call me on 98I0O 12345 after 6", ["98I0O 12345"], ["after 6"])
case("8.03", 8, "salary a/c 5010O234567812 at HDFC", ["5010O234567812"], ["HDFC"])
case("8.04", 8, "card 4l11 1111 1111 1111 was blocked", ["4l11 1111 1111 1111"])
case("8.05", 8, "UAN lOO847362915 is not linked", ["lOO847362915"])
case("8.06", 8, "mobile: 9O8765432l", ["9O8765432l"])
case("8.n1", 8, "The OIL and LOL tickets are closed.", [], ["The OIL and LOL tickets are closed."])
case("8.n2", 8, "Room 1O1 is booked for the interview.", [], ["Room 1O1 is booked for the interview."])
case("8.n3", 8, "Use model X1 or l2 for the batch.", [], ["Use model X1 or l2 for the batch."])


# --- batch B: written after the rules above were built, run once as a generalisation check ---------------------
J1 = iban("ES", "21000418450200051332")
J2 = iban("IE", "AIBK93115212345678")
J3 = iban("BH", "NBOB00001299123456")
J4 = iban("PK", "SCBL0000001123456702")
case("b1.01", 1, "Kindly add Vasudha Joshi as a dependant; she is the wife of Mr. Arvind Nimbalkar.",
     ["Arvind", "Nimbalkar", "Vasudha"])
case("b1.02", 1, "I, Rajendra Singh Rathod s/o Bhanwar Singh Rathod, declare that the details are correct.",
     ["Bhanwar", "Rajendra", "Rathod"], ["declare that the details are correct"])
case("b1.03", 1, "Please share the relieving letter with Jaswinder Kaur Sandhu at the earliest.", ["Jaswinder", "Sandhu"],
     ["relieving letter"], declared=["Jaswinder Sandhu"])
case("b1.04", 1, "Best,\nAnkita\nMoghe", ["Ankita", "Moghe"])
case("b1.05", 1, "Dr. (Ms.) Purnima Banerji has signed the medical certificate.", ["Purnima", "Banerji"],
     ["medical certificate"])
case("b1.06", 1, "The pension is payable to Smt. Gangabai w/o late Shri Dattatray Pawar.", ["Gangabai", "Dattatray"])
case("b1.07", 1, "Yashwant joined in 2019; Yashwant Bhosale is now in Treasury.", ["Bhosale"], ["Treasury"],
     declared=["Yashwant Kadam"])
case("b1.n1", 1, "Nisha Kamat Transfer Request Approved", [], ["Transfer Request Approved"], declared=["Nisha Kamat"])
case("b1.n2", 1, "On Friday Harish Oza Presented The Quarterly Numbers.", [], ["Presented The Quarterly Numbers"],
     declared=["Harish Oza"])
case("b2.01", 2, "Sr No,Employee Name,Designation\n1,Pallavi Thorat,Analyst\n2,Nilesh Gawde,Associate",
     ["Pallavi", "Thorat", "Nilesh", "Gawde"], ["Analyst", "Associate"])
case("b2.02", 2, "Spouse Name | Date of Marriage\nSnehal Patkar | 2015\nRupali Khot | 2018",
     ["Snehal", "Patkar", "Rupali", "Khot"])
case("b2.03", 2, "Buddy\tNew Joiner\nTejal Wani\tAmeya Dixit\nMihir Sathe\tRitu Naidu",
     ["Tejal", "Wani", "Mihir", "Sathe"])
case("b2.n1", 2, "City,Office,Seats\nPune,Baner,120\nMumbai,BKC,80", [], ["Baner", "BKC"])
case("b2.n2", 2, "Leave Type\tBalance\nCasual\t4\nPrivilege\t12", [], ["Casual", "Privilege"])
case("b3.01", 3, "fyi, spoke with mrunal about her notice period buyout.", ["mrunal"], ["notice period"])
case("b3.02", 3, "can u remind hrishita to upload the investment proofs", ["hrishita"], ["investment proofs"])
case("b3.03", 3, "the appraisal of ketaki deshpande is still pending", ["ketaki", "deshpande"])
case("b3.n1", 3, "can u remind payroll to upload the investment proofs", [], ["remind payroll"])
case("b3.n2", 3, "spoke with the vendor about the cab invoices", [], ["spoke with the vendor"])
case("b3.n3", 3, "hello all, the townhall is at 4 pm", [], ["hello all, the townhall is at 4 pm"])
case("b4.01", 4, f"Spanish account for the payout: {group4(J1)}", [group4(J1)])
case("b4.02", 4, f"iban {J2} (Dublin branch)", [J2], ["Dublin branch"])
case("b4.03", 4, f"Bahrain IBAN - {group4(J3, '-')}", [group4(J3, '-')])
case("b4.04", 4, f"PK account: {J4[:4]} {J4[4:8]} {J4[8:]}", [J4[8:]])
case("b4.n1", 4, "Tax code GB12 applies to UK expats only.", [], ["GB12"])
case("b5.01", 5, "Correspondence address - Flat No. 11, Ganesh Krupa Society, Paud Road, Pune 411038", ["11", "411038",
     "Ganesh Krupa Society"])
case("b5.02", 5, "Her family resides at 7-1-45/A, Ameerpet, Hyderabad, Telangana - 500016.", ["7-1-45/A", "500016"])
case("b5.03", 5, "pin code: 380015", ["380015"])
case("b5.n1", 5, "Block 3 of the training calendar is full.", [], ["Block 3 of the training calendar"])
case("b5.n2", 5, "Plot the attrition numbers for 2024 by quarter.", [], ["Plot the attrition numbers for 2024"])
case("b5.n3", 5, "Our Chennai office pincode changed last year.", [], ["Chennai office pincode changed"])
case("b6.01", 6, "Only 52,700/- came this month, please check", ["52,700"])
case("b6.02", 6, "my reimbursement of 6,480 rs is pending", ["6,480"])
case("b6.03", 6, "She received 1.2 million INR as joining bonus.", ["1.2 million"])
case("b6.n1", 6, "The cab fare was 450/- each way.", [], [])
case("b6.n2", 6, "The office has 2,400 seats and 1,800 lockers.", [], ["2,400 seats"])
case("b6.n3", 6, "Training budget is 8,50,000 for the year.", [], ["8,50,000"])
case("b7.01", 7, "D.O.B: 4th Nov '87", ["4th Nov '87"])
case("b7.02", 7, "she was born on twelfth of June 1994", ["twelfth of June 1994"])
case("b7.03", 7, "Date of birth 30-Sep-92 as per the PAN card", ["30-Sep-92"])
case("b7.n1", 7, "The offsite is on 4th Nov '25 in Lonavala.", [], ["4th Nov '25"])
case("b8.01", 8, "PAN linked mobile is 98O1l 23456", ["98O1l 23456"])
case("b8.02", 8, "aadhar: 4O21 7735 l098", ["4O21 7735 l098"])
case("b8.n1", 8, "Use IO 101 in the course catalogue.", [], ["IO 101"])

CASES = C
