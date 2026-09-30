"""hr_free_text templates (v2): emails, chats, notes, forms written the way Indian HR/payroll people write."""
from __future__ import annotations

from .core import E, tpl, rand_date, gen_acct, gen_ifsc, gen_pan, gen_aadhaar, fmt_aadhaar

S = "hr_free_text"


def eid(p):
    return E(p.emp_id, "EMPLOYEE_ID")


@tpl(S, "fnf_settlement_mail")
def _(g):
    b, r = g.b, g.r
    emp, hr = g.person("roster"), g.person()
    b("Subject: FNF - ", g.nm(emp, "lf"), " / ", eid(emp), "\n\nHi ", g.nm(hr, "first"), ",\n\n")
    b("Pls process full & final for ", g.nm(emp), " whose last working day was ", g.date(2025, 2026), ". ")
    b("Last drawn fixed CTC was ", g.money(), g.pick(" p.a.", " per annum", ""), " and the pending variable payout is ", g.money("bonus"), ". ")
    b("Salary account on file: ", E(gen_acct(r), "BANK_ACCOUNT"), " (", E(gen_ifsc(r), "IFSC"), "). PAN for TDS - ",
      E(gen_pan(r, emp), "PAN"), ".\n")
    b(g.pick("Notice shortfall to be recovered as per policy.", "No notice recovery, buyout was approved.", "Laptop return is pending, please hold till IT clears."))
    b("\n\nThanks,\n", g.nm(g.person("roster"), "init"), "\nPeople Ops")


@tpl(S, "bank_change_request")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("From: ", g.mail(emp), "\nTo: payroll@kestrelsys.in\nSubject: change of salary bank a/c\n\n")
    b("Dear Payroll Team,\n\nMy old account has been closed (merger). Kindly credit my salary from this month to the new account.\n\n")
    b("  Name    : ", g.nm(emp, g.pick("full", "upper")), "\n  Emp No  : ", eid(emp), "\n  A/c No  : ", E(gen_acct(r), "BANK_ACCOUNT"),
      "\n  IFSC    : ", E(gen_ifsc(r), "IFSC"), "\n  Mobile  : ", g.phone(emp), "\n\n")
    b("Cancelled cheque attached. ", g.pick("Regards", "Thanks & regards", "Thank you"), ",\n", g.nm(emp, "first"))


@tpl(S, "hinglish_wa_thread")
def _(g):
    b, r = g.b, g.r
    a, c, d = g.person(), g.person("roster"), g.person()
    b("[10:42] hrbp: ", g.nm(a, "lower"), " ka salary revision pending hai kya?\n")
    b("[10:44] payroll: haan yaar, ", g.nm(a, "first"), " ka new ctc ", g.money(style=4), " approve hua hai, ", g.nm(c, "lower"),
      " ne bola tha\n[10:45] hrbp: ok toh ek baar ", g.nm(d, "first"), " se confirm kar lo, unka number ", g.phone(d), " hai\n")
    b("[10:47] payroll: kar diya, emp id ", eid(c), " wale ka bhi dekhna hai\n[10:47] hrbp: haan wo kal karenge, mail bhej dena ", g.mail(c), " pe\n")


@tpl(S, "posh_committee_summary")
def _(g):
    b, r = g.b, g.r
    comp, resp, w1, w2, ic = g.person(), g.person(), g.person(), g.person(), g.person("roster")
    b("ICC PROCEEDINGS - CONFIDENTIAL\nMatter ref: ICC/", str(r.randint(2024, 2026)), "/", str(r.randint(1, 40)), "\n\n")
    b("The complainant, ", g.nm(comp), ", alleges that ", g.nm(resp, g.pick("full", "full", "last")), " made repeated remarks during the Pune offsite. ")
    b("Statements were recorded from ", g.nm(w1), " and ", g.nm(w2, g.pick("full", "first")), ". ")
    b("The respondent denied the allegations; the presiding officer is ", g.nm(ic, "init"), " (", eid(ic), "). ")
    b("Committee recommends a written warning and mandatory training; the complainant may be reached at ", g.phone(comp), " for any follow-up.")


@tpl(S, "bgv_discrepancy")
def _(g):
    b, r = g.b, g.r
    cand, ref = g.person("external"), g.person()
    b("Background verification - discrepancy report\nCandidate: ", g.nm(cand), "\nDOB as per application: ", g.dob(), "\n")
    b("Aadhaar submitted: ", E(fmt_aadhaar(r, gen_aadhaar(r)), "AADHAAR"), "   PAN: ", E(gen_pan(r, cand), "PAN"), "\n")
    b("Employment at ", r.choice(["Quantiva", "Ironbark", "Nimbus Data Works"]), " could not be verified. HR contact provided (", g.nm(ref),
      ") was unreachable on ", g.phone(ref), ", email bounced from ", g.mail(ref), ".\n")
    b("Alt number of candidate: ", g.phone(cand), ". Please advise whether to hold the offer.")


@tpl(S, "offer_negotiation_thread")
def _(g):
    b, r = g.b, g.r
    rec, cand, mgr = g.person(), g.person("external"), g.person()
    b("> ", g.nm(cand, "first"), " wrote:\n> current fixed is ", g.money(style=4), ", expecting ", g.money(style=4), " plus joining bonus\n\n")
    b("Hi ", g.nm(rec, "first"), ", spoke with ", g.nm(mgr), " - we can go up to ", g.money(), " fixed and a one-time joining bonus of ", g.money("bonus"),
      ". Candidate's number is ", g.phone(cand), ", personal mail ", g.mail(cand), ". ", g.pick("Please draft the letter today.", "Need this closed by Friday."), "\n")


@tpl(S, "insurance_enrolment")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    sp, ch = g.person("external"), g.person("external")
    b("Group Mediclaim - member enrolment\nPrimary member : ", g.nm(emp), " [", eid(emp), "]\nMobile         : ", g.phone(emp), "\nEmail          : ", g.mail(emp), "\n\n")
    b("Dependents:\n 1. ", g.nm(sp, "full"), " (spouse), born ", g.dob(), "\n 2. ", g.nm(ch, "full"), " (child), born ", g.dob(d=rand_date(r, 2008, 2022)), "\n")
    b("\nSum insured 5 lakh floater. Parents cover opted: ", g.pick("yes", "no"), ".")


@tpl(S, "it_joiner_credentials")
def _(g):
    b, r = g.b, g.r
    new, it = g.person("roster"), g.person()
    b(g.nm(it, "first"), ": account created for ", g.nm(new), " (", eid(new), ")\nlogin: ", g.mail(new), "\ntemp pwd: ", g.secret("password"),
      "\nask them to change on first login, do not forward this msg\n")
    if r.random() < 0.5:
        b("vpn seed: ", g.secret("hex32"), "\n")


@tpl(S, "medical_leave_note")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("Medical leave intimation\n\nEmployee ", g.nm(emp, "full"), " (", eid(emp), ") has been advised rest from ", g.date(2025, 2026), " to ", g.date(2025, 2026),
      " by the treating physician. DOB on file ", g.dob(), ". Reachable on ", g.phone(emp), ".\nFitness certificate needed before rejoining.")


@tpl(S, "tax_proof_mail")
def _(g):
    b, r = g.b, g.r
    emp = g.person()
    b("Subject: 80C / HRA proofs FY 24-25\n\nHello team,\n\nAttaching rent receipts and LIC premium. My PAN is ", E(gen_pan(r, emp), "PAN"),
      " (name as per PAN: ", g.nm(emp, "upper"), "). Gross salary as per Form 16 Part B is ", g.money(style=0), ".\nPlease confirm the TDS you will deduct in the March cycle.\n\nRegards\n", g.nm(emp, "first"), " | ", g.phone(emp))


@tpl(S, "pf_withdrawal_form_mail")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("Hi HR, I want to file the PF final settlement (Form 19) after resignation. Details:\nAadhaar ", E(fmt_aadhaar(r, gen_aadhaar(r), 1), "AADHAAR"),
      ", PAN ", E(gen_pan(r, emp), "PAN"), ", bank ", E(gen_acct(r), "BANK_ACCOUNT"), " / ", E(gen_ifsc(r), "IFSC"),
      ". Employee code ", eid(emp), ", mobile ", g.phone(emp), " (linked to UAN).\n- ", g.nm(emp, "init"))


@tpl(S, "salary_advance_ask")
def _(g):
    b, r = g.b, g.r
    emp, mgr = g.person("roster"), g.person()
    b("Sir/Madam,\n\nI request an advance of ", g.money("monthly"), " against my salary for a medical emergency in the family. My current gross is ", g.money("monthly"),
      " per month. Please credit to my UPI ", E(g.v("UPI_ID", p=emp), "UPI_ID"), " or the salary account.\n\nApproved by ", g.nm(mgr, "last"),
      " on the phone. ", g.nm(emp, "full"), ", ", eid(emp), "\n")


@tpl(S, "recruiter_profile_share")
def _(g):
    b, r = g.b, g.r
    rec, c1, c2 = g.person("external"), g.person("external"), g.person("external")
    b("Hi, sharing two profiles for the Backend Lead opening.\n\n1) ", g.nm(c1), " - ", str(r.randint(6, 14)), " yrs, cur ", g.money(style=4), ", exp ", g.money(style=4),
      ", NP 60 days. Ph ", g.phone(c1), ", ", g.mail(c1), "\n2) ", g.nm(c2, "full"), " - ", str(r.randint(4, 9)), " yrs, cur ", g.money(style=4), ", exp ", g.money(style=4),
      ", serving. Ph ", g.phone(c2), ", ", g.mail(c2), "\n\nBest,\n", g.nm(rec, "first"), " | Corvus Recruitment | ", g.phone(rec), "\n")


@tpl(S, "promotion_committee_minutes")
def _(g):
    b, r = g.b, g.r
    ps = [g.person("roster") for _ in range(3)]
    ch = g.person()
    b("Minutes - talent review, chaired by ", g.nm(ch, "init"), "\n\n")
    for i, p in enumerate(ps, 1):
        b(f"{i}. ", g.nm(p, g.pick("full", "full", "last")), " (", eid(p), ") - ", g.pick("promote to Sr Manager", "retain, no change", "promote to Lead"), "; revised fixed ", g.money(), ", earlier ", g.money(), ".\n")
    b("\nAction: HRBP to prepare letters by month end.")


@tpl(S, "reference_call_notes")
def _(g):
    b, r = g.b, g.r
    cand, ref, rec = g.person("external"), g.person("external"), g.person()
    b("Reference check call notes (", g.nm(rec, "first"), ")\n\nSpoke to ", g.nm(ref, g.pick("full", "full", "init")), ", who managed ", g.nm(cand, g.pick("full", "first")),
      " for three years at ", r.choice(["Solstice Learning", "Terrapin Software", "Aurum Wealth"]), ". Said the candidate is a strong closer but needs support on documentation. ")
    b("Rehire: yes. Referee's direct line ", g.phone(ref), ", mail ", g.mail(ref), ". Candidate's last drawn was ", g.money(style=4), " as stated by referee.")


@tpl(S, "contractor_payout_mail")
def _(g):
    b, r = g.b, g.r
    v, fin = g.person("external"), g.person()
    b("Hi ", g.nm(fin, "first"), ",\n\nThe freelance designer (", g.nm(v), ") has raised her final invoice. She prefers UPI: ", E(g.v("UPI_ID", p=v), "UPI_ID"),
      ". If that fails use a/c ", E(gen_acct(r), "BANK_ACCOUNT"), ", ", E(gen_ifsc(r), "IFSC"), ". Her email is ", g.mail(v), ", phone ", g.phone(v),
      ".\nAmount as per SOW.\n")


@tpl(S, "resignation_accept")
def _(g):
    b, r = g.b, g.r
    emp, mgr = g.person("roster"), g.person()
    b("Dear ", g.nm(emp, "first"), ",\n\nThis acknowledges your resignation dated ", g.date(2025, 2026), ". Your last working day will be ", g.date(2025, 2026),
      " subject to a 90-day notice. Employee ID ", eid(emp), ". Your reporting manager ", g.nm(mgr), " will coordinate the handover.\n\nYou are entitled to the retention bonus of ",
      g.money("bonus"), " only if you serve the full notice.\n\nHR")


@tpl(S, "teams_hrbp_sync")
def _(g):
    b, r = g.b, g.r
    a, c = g.person("roster"), g.person()
    b("hey - quick one. ", g.nm(a, "lower"), " said ", g.nm(c, "first"), " is asking why bonus came as ", g.money("bonus"), " instead of ", g.money("bonus"),
      ". can u check emp ", eid(a), " in the payroll register? her phone is ", g.phone(c), " if u need to call\nok will do, ping in 10")


@tpl(S, "corp_card_issue")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("Corporate card request approved.\nCardholder: ", g.nm(emp, "upper"), " (", eid(emp), ")\nCard: ", E(g.v("CARD"), "CARD"), " valid thru ", str(r.randint(1, 12)), "/", str(r.randint(27, 31)),
      "\nSpend limit as per grade. Statement mails go to ", g.mail(emp), ".")


@tpl(S, "relocation_offer_mail")
def _(g):
    b, r = g.b, g.r
    emp, mob = g.person(), g.person("external")
    b("Subject: relocation Bengaluru -> Hyderabad\n\nHi ", g.nm(emp, "first"), ", the relocation package is ", g.money("bonus"), " lumpsum plus 30 days temp stay. ")
    b("Vendor contact: ", g.nm(mob), " from Redwood Relocations (", g.phone(mob), "). Please share your alternate number too.\n")


@tpl(S, "calibration_notes")
def _(g):
    b, r = g.b, g.r
    ps = [g.person() for _ in range(3)]
    b("Calibration - engineering, cycle H1\n")
    for p in ps:
        b("* ", g.nm(p, "full"), ": rated ", g.pick("4 - exceeds", "3 - meets", "5 - outstanding"), "; salary moves from ", g.money(), " to ", g.money(), "\n")
    b("Note: ", g.nm(ps[0], "last"), "'s manager wants an off-cycle review.")


@tpl(S, "dob_fix_request")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("Sub: DOB correction in HRMS\n\nMy date of birth is wrongly captured as ", g.dob(), " in the system, it should be ", g.dob(),
      " as per my Aadhaar ", E(fmt_aadhaar(r, gen_aadhaar(r)), "AADHAAR"), ". Ref emp id ", eid(emp), ". Reach me at ", g.mail(emp), " or ", g.phone(emp), ".\n\n", g.nm(emp))


@tpl(S, "long_form_top_label")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("NEW JOINER STATUTORY FORM - PART C\n"
      "All entries under 'Personal Identifiers' are the joiner's Aadhaar numbers; entries under 'Financial' are the salary bank account numbers.\n"
      "Please do not write anything other than the number.\n\n")
    for i in range(r.randint(20, 26)):
        b(g.pick("Instruction ", "Note ", "Declaration "), str(i + 1), ": ", g.pick("Attach a photocopy where asked.", "Original to be shown to HR.", "Leave blank if not applicable.",
                                                                                    "Use black ink and capital letters.", "Sign at the bottom of each page."), "\n")
    b("\nPersonal Identifiers\n    ", E(fmt_aadhaar(r, gen_aadhaar(r)), "AADHAAR"), "\n\nFinancial\n    ", E(gen_acct(r), "BANK_ACCOUNT"), "\n\nSigned: ", g.nm(emp), " (", eid(emp), ")")


@tpl(S, "legend_then_rows")
def _(g):
    b, r = g.b, g.r
    b("Key for the attached extract (read before using):\n  c1 = employee id, c2 = PAN, c3 = mobile, c4 = date of birth\n")
    for _ in range(r.randint(3, 6)):
        b("(this extract is refreshed nightly; do not edit by hand)\n")
    b("Sr|c1|c2|c3|c4\n")
    for i in range(r.randint(22, 28)):
        p = g.person("roster")
        b(f"{i + 1}|", eid(p), "|", E(gen_pan(r, p), "PAN"), "|", g.phone(p, 8), "|", g.dob(2), "\n")
    b("-- end of extract --")


@tpl(S, "number_first_explained_later")
def _(g):
    b, r = g.b, g.r
    cand, hr = g.person("external"), g.person()
    b(g.phone(cand, 0), " - ", g.pick("this one", "that's it", "use this"), ".\n\n")
    b("Hi ", g.nm(hr, "first"), ", as discussed, the number above is ", g.nm(cand, "first"), "'s personal mobile; her Aadhaar is ", E(fmt_aadhaar(r, gen_aadhaar(r), 0), "AADHAAR"),
      " as shared on WhatsApp. Please update the ATS record.")


@tpl(S, "helpdesk_case_export")
def _(g):
    b, r = g.b, g.r
    emp, agent = g.person("roster"), g.person()
    b("Case HR", str(r.randint(100000, 999999)), " | Priority ", g.pick("P2", "P3"), " | State: In Progress\nCaller: ", g.nm(emp, "lf"), " | ", eid(emp), " | ", g.phone(emp, 3),
      "\nAssigned to: ", g.nm(agent), "\nShort description: payslip shows wrong tax slab\nWork notes: Employee says annual gross ", g.money(), ". Callback email ", g.mail(emp), ".")


@tpl(S, "chat_paste_credentials")
def _(g):
    b, r = g.b, g.r
    a, c = g.person(), g.person()
    b(g.nm(a, "first"), " > sharing the sftp login for the bank file, dont paste elsewhere\n", g.nm(a, "first"), " > user: hrsvc, pass: ", g.secret("password"), "\n",
      g.nm(c, "first"), " > got it, api key for the attendance vendor is ", g.secret("hex40"), " right?\n", g.nm(a, "first"), " > yes, mail me at ", g.mail(a), " if it fails")


@tpl(S, "joining_docs_checklist")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("Joining kit received - ", g.nm(emp), " (", eid(emp), ")\n[x] Photo ID: Aadhaar ", E(fmt_aadhaar(r, gen_aadhaar(r)), "AADHAAR"), "\n[x] PAN card: ", E(gen_pan(r, emp), "PAN"),
      "\n[x] Cancelled cheque: a/c ", E(gen_acct(r), "BANK_ACCOUNT"), ", ", E(gen_ifsc(r), "IFSC"), "\n[ ] Form 11 - pending\nDOB per SSC certificate: ", g.dob(), "\nMobile ", g.phone(emp), ", personal email ", E(g.ext_email(emp), "EMAIL"), "\n")


@tpl(S, "gratuity_letter")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("To whom it may concern\n\nThis is to certify that ", g.hon(emp), g.nm(emp, "full"), " (", eid(emp), ") completed ", str(r.randint(5, 24)), " years of continuous service. The gratuity payable is ", g.money("bonus", 0),
      " and the last drawn basic was ", g.money("monthly"), ". Nominee: ", g.nm(g.person("external")), ".\n")


@tpl(S, "travel_booking_request")
def _(g):
    b, r = g.b, g.r
    emp, comp = g.person(), g.person("external")
    b("Please book BLR-DEL for ", g.nm(emp, "mid"), " and a client guest, ", g.nm(comp, "full"), " (DOB ", g.dob(), ", contact ", g.phone(comp, 6), "). Pay with corporate card ", E(g.v("CARD", style=1), "CARD"),
      ". Send e-tickets to ", g.mail(emp), ".")


@tpl(S, "grievance_hinglish_email")
def _(g):
    b, r = g.b, g.r
    emp, mgr = g.person("roster"), g.person()
    b("Respected HR,\n\nmera naam ", g.nm(emp, "full"), " hai, emp code ", eid(emp), ". ", g.nm(mgr, "full"), " sir mujhe roz late night calls karte hain aur appraisal mein low rating ki dhamki dete hain. Mera salary ",
      g.money(), " hai lekin extra kaam ka koi compensation nahi. Kripya action lein. Contact: ", g.phone(emp), ".\n")


@tpl(S, "wellness_vendor_thread")
def _(g):
    b, r = g.b, g.r
    v, hr = g.person("external"), g.person()
    c, dom = g.corp()
    b("From: ", g.nm(v, "full"), " <", E(g.ext_email(v, dom), "EMAIL"), ">\nTo: ", g.mail(hr), "\n\nDear ", g.nm(hr, "first"), ", thanks for the call. For the EAP tie-up we will need a nodal contact and a PO. My cell: ", g.phone(v), ". - ", g.nm(v, "first"), ", ", c)


@tpl(S, "attrition_review_mail")
def _(g):
    b, r = g.b, g.r
    a, a2, hr = g.person("roster"), g.person("roster"), g.person()
    b("Attrition review: ", g.nm(a, "full"), " (", eid(a), ") and ", g.nm(a2, "full"), " (", eid(a2), ") both resigned within a month. ", g.nm(hr, "last"), " flagged that their fixed pay of ",
      g.money(), " and ", g.money(), " sits below band midpoint. Exit interviews will be led by ", g.nm(g.person(), "first"), ".")


@tpl(S, "wellness_reimbursement")
def _(g):
    b, r = g.b, g.r
    emp = g.person("roster")
    b("Gym / wellness reimbursement claim from ", g.nm(emp, "upper"), " (", eid(emp), ")\nPay to UPI ", E(g.v("UPI_ID", p=emp), "UPI_ID"), " (registered mobile ", g.phone(emp, 4), ").\nInvoice paid on card ", E(g.v("CARD", style=2), "CARD"), ".\nReceipts attached; approver ", g.nm(g.person("roster"), "last"), ".")
