"""ner_test2 templates: free text whose PERSON names are all absent from the roster (candidates, relatives, vendors,
ex-employees, referees).  Names mostly appear mid-sentence, without honorifics or greetings."""
from __future__ import annotations

from .core import E, ner_tpl, gen_acct, gen_ifsc, gen_pan, rand_date, fmt_date

F = ("full", "full", "full", "first", "first", "last", "init", "init3", "lower")


def f(g):
    return g.pick(*F)


@ner_tpl("n_interview_feedback")
def _(g):
    b, r = g.b, g.r
    c, i1, i2 = g.person(), g.person(), g.person()
    b("Interview feedback - ", g.pick("SDE-2", "Product Analyst", "Payroll Specialist", "Data Engineer"), " round ", str(r.randint(1, 3)), "\n\nCandidate ", g.nm(c, "full"), " was strong on fundamentals. ",
      g.nm(i1, f(g)), " felt the system design answers were thin, while ", g.nm(i2, f(g)), " thought ", g.nm(c, "first"), " could grow into the role. ",
      g.pick("Recommend hold.", "Recommend move forward.", "Split decision."), " Candidate can be reached on ", g.phone(c), " or ", g.mail(c), ".")


@ner_tpl("n_bereavement_case_note")
def _(g):
    b, r = g.b, g.r
    a, sp, rel = g.person(), g.person(), g.person()
    b("Case note: employee called in this morning, very upset. Her father-in-law ", g.nm(rel, g.pick("full", "init")), " passed away in the night; husband ", g.nm(sp, "first"),
      " is travelling to the village. Advised to apply for bereavement leave and told her that ", g.nm(a, "first"), " from the team will cover the handover. Family contact for updates: ", g.phone(sp), ".")


@ner_tpl("n_hinglish_chat")
def _(g):
    b, r = g.b, g.r
    a, c, d = g.person(), g.person(), g.person()
    b("arre ", g.nm(a, "lower"), " ne bola ki ", g.nm(c, "lower"), " kal nahi aayega\nkyun? uski maa ki tabiyat kharab hai kya\nhaan, aur ", g.nm(d, "lower"), " ko bhi bata dena, wo sab sambhal lega\nok done. ", g.nm(a, "lower"), " ka number ", g.phone(a), " hai agar call karna ho")


@ner_tpl("n_talent_meeting_notes")
def _(g):
    b, r = g.b, g.r
    ps = [g.person() for _ in range(4)]
    b("Notes from TA sync (", fmt_date(r, rand_date(r, 2025, 2026), 4), ")\n- pipeline for the data role: ", g.nm(ps[0], "full"), " is at offer stage, expecting ", g.money(style=4), "\n- ", g.nm(ps[1], f(g)), " withdrew after a counter from another company\n- sourcing via referrals; ",
      g.nm(ps[2], "init"), " (ex-colleague of the hiring manager) has shared two profiles\n- background check for ", g.nm(ps[3], "full"), " still pending, PAN on file ", E(gen_pan(r, ps[3]), "PAN"), "\n")


@ner_tpl("n_referral_note")
def _(g):
    b, r = g.b, g.r
    ref, c = g.person(), g.person()
    b("Referral: ", g.nm(ref, "init"), " worked with the candidate at his previous company and vouches for him. Candidate name is ", g.nm(c, "full"), ", DOB ", g.dob(4), ", mobile ", g.phone(c), ". Currently at ",
      g.pick("Northgate Analytics", "Nimbus Data Works", "Solstice Learning"), " on ", g.money(style=4), ". ", g.pick("Please fast-track.", "No urgency."), "\n")


@ner_tpl("n_reference_check")
def _(g):
    b, r = g.b, g.r
    ref, c, rec = g.person(), g.person(), g.person()
    b("Spoke with ", g.nm(ref, "full"), " (former line manager) about ", g.nm(c, f(g)), ". He described ", g.nm(c, "first"), " as reliable and said the notice period is negotiable. ", g.nm(ref, "last"),
      " asked us to email confirmation to ", g.mail(rec), ". Verified via the number ", g.phone(ref), ".")


@ner_tpl("n_vendor_email")
def _(g):
    b, r = g.b, g.r
    v, v2, c = g.person(), g.person(), g.person()
    co, dom = g.corp()
    b("Subject: Re: background verification TAT\n\nThanks for the reminder. Our field agent ", g.nm(v2, "full"), " visited the address on Tuesday but ", g.nm(c, "first"), " was not home; the neighbour, ", g.nm(g.person(), f(g)),
      ", said the family moved. We will retry. Please route queries to me at ", E(g.ext_email(v, dom), "EMAIL"), " or ", g.phone(v), ".\n\n", g.nm(v, "full"), "\n", co, "\n")


@ner_tpl("n_alumni_note")
def _(g):
    b, r = g.b, g.r
    x, y = g.person(), g.person()
    b("Alumni tracker: ", g.nm(x, "full"), " left us in ", str(r.randint(2019, 2025)), " and is now Director at ", g.pick("Aurum Wealth Advisors", "Zephyr Cloud Labs", "Ironbark Engineering"), ". Her successor ",
      g.nm(y, f(g)), " says ", g.nm(x, "first"), " still helps with escalations. Personal mail on record: ", g.mail(x), ".")


@ner_tpl("n_incident_witnesses")
def _(g):
    b, r = g.b, g.r
    a, b2, c = g.person(), g.person(), g.person()
    b("Security desk incident log. At 22:40 the guard on duty, ", g.nm(a, "full"), ", noticed that ", g.nm(b2, f(g)), " had tailgated through gate 2 with a visitor named ", g.nm(c, "full"),
      ". Visitor register shows mobile ", g.phone(c), ". ", g.nm(a, "last"), " has been asked to write a statement.")


@ner_tpl("n_buddy_intro_mail")
def _(g):
    b, r = g.b, g.r
    n, buddy, mgr = g.person(), g.person(), g.person()
    b("Hello all,\n\nPlease welcome ", g.nm(n, "full"), " who joins the Chennai payroll team on Monday. ", g.nm(buddy, f(g)), " will be the onboarding buddy and ", g.nm(mgr, "full"), " continues as manager. ",
      "Ping ", g.nm(buddy, "first"), " on ", g.phone(buddy), " for laptop pickup.\n")


@ner_tpl("n_customer_escalation")
def _(g):
    b, r = g.b, g.r
    cu, rep, rep2 = g.person(), g.person(), g.person()
    b("Escalation #", str(r.randint(10000, 99999)), ": client rep ", g.nm(cu, "full"), " called twice about the delayed onboarding kit. ", g.nm(rep, f(g)), " promised a callback, but ", g.nm(rep2, "first"),
      " picked up the second call and didn't have context. Client email: ", g.mail(cu), ". Please assign to ", g.nm(g.person(), "init"), ".")


@ner_tpl("n_wa_group_lowercase")
def _(g):
    b, r = g.b, g.r
    a, c, d, e = g.person(), g.person(), g.person(), g.person()
    b("[09:12] ", g.nm(a, "first"), ": guys ", g.nm(c, "lower"), " ka referral aaya hai, ", g.nm(d, "lower"), " ke saath kaam kiya tha pehle\n[09:13] ", g.nm(e, "first"), ": haan maine dekha, cv bhej do na\n[09:15] ", g.nm(a, "first"), ": cv ", g.mail(c), " pe hai, ph ", g.phone(c), "\n")


@ner_tpl("n_legal_notice_summary")
def _(g):
    b, r = g.b, g.r
    adv, cl, ex = g.person(), g.person(), g.person()
    b("Legal summary: advocate ", g.nm(adv, "full"), " has sent a notice on behalf of ", g.nm(cl, "full"), ", a former contractor, claiming unpaid dues of ", g.money("bonus"), ". The ex-employee ", g.nm(ex, f(g)),
      " is named as a witness. Reply due by ", fmt_date(r, rand_date(r, 2026, 2026), 4), ". Advocate's office: ", g.phone(adv), ".")


@ner_tpl("n_event_speaker_mail")
def _(g):
    b, r = g.b, g.r
    s1, s2, s3 = g.person(), g.person(), g.person()
    b("Speakers confirmed for the L&D summit: ", g.nm(s1, "init"), " (keynote), ", g.nm(s2, "init3"), " (panel) and ", g.nm(s3, "full"), " (workshop). ", g.nm(s2, "last"), " needs a hotel booking for two nights; bill to card on file.\n")


@ner_tpl("n_family_emergency_contacts")
def _(g):
    b, r = g.b, g.r
    e, m, s = g.person(), g.person(), g.person()
    b("Emergency contacts noted for the new joiner: mother ", g.nm(m, "full"), " (", g.phone(m), ") and brother ", g.nm(s, f(g)), ". If neither answers try ", g.nm(e, "first"), "'s office line. Blood group not disclosed.\n")


@ner_tpl("n_exit_interview_free")
def _(g):
    b, r = g.b, g.r
    a, m1 = g.person(), g.person()
    b("Exit interview with ", g.nm(a, "full"), ": main reasons were commute and a better offer of ", g.money(style=4), ". Says working under ", g.nm(m1, f(g)), " was good but the team lacked direction after ",
      g.nm(g.person(), "first"), " left. Would consider returning. Personal contact ", g.phone(a), ".\n")


@ner_tpl("n_bank_kyc_vendor_note")
def _(g):
    b, r = g.b, g.r
    v, o = g.person(), g.person()
    b("Vendor KYC done for ", g.nm(v, "full"), " (proprietor). Cancelled cheque shows ", E(gen_acct(r), "BANK_ACCOUNT"), " / ", E(gen_ifsc(r), "IFSC"), ". Introduced by ", g.nm(o, f(g)), ". UPI for small payouts: ", E(g.v("UPI_ID", p=v), "UPI_ID"), ".\n")
