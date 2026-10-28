"""Content-gate data: sentence/document level multi-label classification of special-category and purpose-dependent
content. Labels: labels.GATE_LABELS (mapping to the gateway's PURPOSE_DEPENDENT stances: labels.GATE_TO_PURPOSE_DEPENDENT).

Labelling rule (documented because the classifier is only as good as this rule): a positive label applies when the text
discloses that attribute of a specific person (an employee, a candidate, a family member) or a specific case. A generic
policy statement about the topic ("the health insurance window opens in April", "performance ratings are on a 5 point
scale") is NONE. Texts without any sensitive attribute are NONE.

Two sources, both with labels known BY CONSTRUCTION:
  1. programmatic: positive and neutral seed sentences below, combined and filled (no GPU needed);
  2. LLM templates: the prompt asks for text expressing exactly the requested labels; `validate_gate_template` then
     requires a lexical cue per requested label, so a model that ignores the instruction is dropped, not trusted.
"""
from __future__ import annotations

import random
import re

from .io import make_gate_record
from .labels import GATE_POSITIVE
from .synth import TEMPLATE_DELIM, derive_rng, fill_template, slot_help, validate_template
from .genres import LANG_MIX_NOTES, PERSONAS, TONES, GenreSpec

SEEDS: dict[str, list[tuple[str, str]]] = {
    "HEALTH": [
        ("en", "{{PERSON_1}} has been advised two weeks of bed rest after surgery; please mark the absence as medical leave."),
        ("en", "My diabetes has worsened and the doctor wants me on dialysis thrice a week, so I need flexible shifts. - {{PERSON_1}}"),
        ("en", "Employee {{EMPLOYEE_ID_1}} was diagnosed with dengue and is admitted at the hospital; the discharge summary is attached."),
        ("hi-Latn", "Mujhe bukhar aur typhoid hai, doctor ne 5 din ka rest bola hai, please leave de do. {{PERSON_1}}"),
        ("en", "{{PERSON_1}} is undergoing treatment for depression and asked that his manager is not told the reason for leave."),
        ("en", "Reimbursement for chemotherapy sessions of my father {{PERSON_2}}; bills enclosed. - {{PERSON_1}}"),
        ("en", "The company doctor found {{PERSON_1}} unfit for night shifts because of a heart condition."),
        ("hi-Latn", "Meri wife {{PERSON_2}} ko TB hai, isliye mujhe ghar par rehna padega."),
    ],
    "DISABILITY": [
        ("en", "{{PERSON_1}} has 60% locomotor disability and requires a ground-floor workstation as a reasonable accommodation."),
        ("en", "Employee {{EMPLOYEE_ID_1}} is visually impaired and uses screen-reader software; disability certificate attached."),
        ("en", "I am hearing impaired and request that team meetings carry live captions. {{PERSON_1}}"),
        ("hi-Latn", "Mere bete {{PERSON_2}} ko autism hai, isliye mujhe special school ke liye timing flexibility chahiye."),
        ("en", "UDID card number is on file for {{PERSON_1}}, who is wheelchair user, so please allot the accessible parking slot."),
        ("en", "Candidate {{PERSON_1}} disclosed dyslexia and asked for extra time in the written assessment."),
    ],
    "PREGNANCY": [
        ("en", "{{PERSON_1}} is expecting in October and requests maternity leave of 26 weeks starting 1 September."),
        ("en", "She is in her second trimester and the doctor has restricted travel; please do not assign the client visit to {{PERSON_1}}."),
        ("hi-Latn", "Main pregnant hoon aur mujhe maternity leave chahiye, due date next month hai. {{PERSON_1}}"),
        ("en", "Paternity leave requested by {{PERSON_1}} as his wife {{PERSON_2}} delivered a baby girl yesterday."),
        ("en", "Miscarriage leave under the policy has been applied for by {{PERSON_1}}; keep this confidential."),
    ],
    "SEXUAL_ORIENTATION": [
        ("en", "{{PERSON_1}} shared that he is gay and asked that his partner be added as a dependant in the insurance policy."),
        ("en", "The complaint alleges that {{PERSON_1}} was mocked in the team for being a lesbian."),
        ("en", "{{PERSON_1}} is transgender and requests that the HR records show the new name and gender."),
        ("hi-Latn", "Unhone bataya ki woh bisexual hain aur isse office me log unhe pareshan karte hain. {{PERSON_1}}"),
        ("en", "Our records note that {{PERSON_1}} identifies as non-binary and uses they/them pronouns."),
    ],
    "RELIGION": [
        ("en", "{{PERSON_1}} requests leave for Eid and Friday prayer breaks; he is a practising Muslim."),
        ("en", "As a Christian employee I would like Sundays off for church. - {{PERSON_1}}"),
        ("hi-Latn", "Main Sikh hoon, mujhe turban pehenne ki permission rehni chahiye, safety helmet ka issue hai."),
        ("en", "The employee {{PERSON_1}} converted to Buddhism last year and wants the HR record updated."),
        ("en", "Employee {{PERSON_1}} observes Ramzan fasting and asked for adjusted lunch-hour shifts."),
    ],
    "CASTE": [
        ("en", "{{PERSON_1}} belongs to the Scheduled Caste category and has submitted the caste certificate for the reservation benefit."),
        ("en", "The grievance says that {{PERSON_1}} faced casteist remarks from the supervisor because of his Dalit background."),
        ("hi-Latn", "Mera caste certificate OBC category ka hai, please records me update kar do. {{PERSON_1}}"),
        ("en", "Employee {{EMPLOYEE_ID_1}} is from a Scheduled Tribe community and is applying under the quota."),
        ("en", "Please ensure that {{PERSON_1}} is not seated with a person of another caste, said the complainant."),
    ],
    "POLITICAL": [
        ("en", "{{PERSON_1}} campaigned for a political party in the state election and a colleague complained about it."),
        ("en", "The employee is a member of the opposition party and was warned about posting political views on the company channel."),
        ("hi-Latn", "{{PERSON_1}} party ke rally me gaye the aur office me politics pe bahas hui."),
        ("en", "Leave to contest the panchayat election on a party ticket was requested by {{PERSON_1}}."),
        ("en", "HR noted that {{PERSON_1}} openly supports a particular political party at work."),
    ],
    "UNION_MEMBERSHIP": [
        ("en", "{{PERSON_1}} is the secretary of the plant workers' union and requested leave for the union conference."),
        ("en", "Management should not know that I have joined the labour union. - {{PERSON_1}}"),
        ("hi-Latn", "{{PERSON_1}} union ka member hai aur strike ke liye workers ko bula raha tha."),
        ("en", "Three employees including {{PERSON_1}} were seen distributing union membership forms in the cafeteria."),
        ("en", "Monthly trade union dues are being deducted from the salary of employee {{EMPLOYEE_ID_1}}."),
    ],
    "BIOMETRIC": [
        ("en", "{{PERSON_1}}'s fingerprint template could not be enrolled on the attendance machine; please use the face scan instead."),
        ("en", "Please delete the retina scan and face data collected from {{PERSON_1}} after the access-card migration."),
        ("hi-Latn", "Mera thumb impression attendance machine me match nahi ho raha, fingerprint dobara lo. {{PERSON_1}}"),
        ("en", "Biometric enrolment (iris and fingerprint) for employee {{EMPLOYEE_ID_1}} was completed at the front gate."),
        ("en", "The face recognition log shows {{PERSON_1}} entering the lab at 02:10 am."),
    ],
    "CRIMINAL_OR_DISCIPLINARY": [
        ("en", "{{PERSON_1}} was issued a show-cause notice and, after the inquiry, suspended for three days for misconduct."),
        ("en", "The background check revealed a pending criminal case against candidate {{PERSON_1}} under section 420."),
        ("hi-Latn", "{{PERSON_1}} ko chori ke aarop me termination letter diya gaya hai."),
        ("en", "Employee {{EMPLOYEE_ID_1}} received a final written warning for repeated absence without information."),
        ("en", "An FIR was lodged against {{PERSON_1}} for assault, and the disciplinary committee will meet on Monday."),
        ("en", "{{PERSON_1}} was convicted in 2019 and did not declare it in the joining form."),
    ],
    "PERFORMANCE_RATING": [
        ("en", "{{PERSON_1}} received a rating of 2 (Needs Improvement) in this year's appraisal and is placed on a PIP."),
        ("en", "Manager's note: employee {{EMPLOYEE_ID_1}} is a top performer, rated 5/5 and eligible for promotion."),
        ("hi-Latn", "{{PERSON_1}} ka performance bahut kharab hai, is saal rating 1 mili hai."),
        ("en", "Calibration moved {{PERSON_1}} from 'Meets Expectations' to 'Exceeds Expectations'."),
        ("en", "KRA score for {{PERSON_1}}: 62%. Bell-curve bucket: bottom 10%."),
    ],
    "FINANCIAL_HARDSHIP": [
        ("en", "I am requesting a salary advance because I cannot pay my son's hospital bill and I am deep in debt. - {{PERSON_1}}"),
        ("en", "The bank has attached the salary of employee {{EMPLOYEE_ID_1}} for loan default; recovery notice enclosed."),
        ("hi-Latn", "Mere upar bahut karza hai, EMI bounce ho gayi hai, thoda advance mil sakta hai kya? {{PERSON_1}}"),
        ("en", "{{PERSON_1}} is struggling financially after the family business closed and asked for hardship assistance."),
        ("en", "Wage garnishment order received for {{PERSON_1}} due to unpaid personal loan."),
    ],
    "MINOR": [
        ("en", "Nominee {{PERSON_2}} is a minor, aged 9 (date of birth {{DATE_OF_BIRTH_1}}); her guardian is {{PERSON_1}}."),
        ("en", "My 7 year old daughter {{PERSON_2}} will be added as a dependant in the health cover. - {{PERSON_1}}"),
        ("hi-Latn", "Mera beta {{PERSON_2}} abhi 12 saal ka hai, uska naam insurance me daalna hai."),
        ("en", "The intern {{PERSON_1}} is 17 years old; parental consent from the guardian is attached."),
        ("en", "Child care allowance is requested for my son {{PERSON_2}}, a minor born on {{DATE_OF_BIRTH_1}}."),
    ],
}
NEUTRAL: list[tuple[str, str]] = [
    ("en", "Please share the updated holiday list for the Bengaluru office; {{PERSON_1}} will circulate it."),
    ("en", "The health insurance enrolment window opens on the first of April and closes in three weeks."),
    ("en", "Performance reviews follow the standard five-point scale; the cycle starts next month."),
    ("en", "{{PERSON_1}} will join the finance team on the 1st as a Senior Analyst, reporting to {{PERSON_2}}."),
    ("en", "Reminder: the union of the two payroll files is stored in the shared folder, ticket {{NEG_TICKET_1}}."),
    ("en", "Section 80C allows a deduction up to the statutory limit; submit proofs by the deadline."),
    ("en", "The fingerprint scanner on floor 3 is being replaced; helpline {{NEG_HELPLINE_1}}."),
    ("en", "Lunch menu for Diwali week and the cultural event schedule are attached."),
    ("hi-Latn", "Kal ki meeting 11 baje hai, {{PERSON_1}} room book kar denge."),
    ("en", "Please confirm the working days in {{NEG_DATE_1}} so that payroll can be processed."),
    ("en", "Ramesh Textiles Pvt Ltd has submitted the invoice; the {{NEG_INVOICE_1}} is pending approval."),
    ("en", "Kindly update my bank details; employee id {{EMPLOYEE_ID_1}}, new account {{BANK_ACCOUNT_1}}."),
    ("en", "A disciplinary policy exists in the handbook and every employee must read it once a year."),
    ("en", "{{PERSON_1}} applied for casual leave on Friday for a family function."),
    ("en", "The company is a minor shareholder in the joint venture and the minor repairs budget is {{NEG_STATUTORY_1}}."),
]

# Lexical cues: an LLM-written positive template is kept only if each requested label has one cue in the text.
CUES: dict[str, re.Pattern] = {k: re.compile(v, re.I) for k, v in {
    "HEALTH": r"surg|diagnos|doctor|hospital|fever|treat|illness|disease|cancer|diabet|dialysis|depress|medical|tb\b|typhoid|dengue|heart|chemo|bukhar|tabiyat|medicine|therapy|anxiety|injur|fracture",
    "DISABILITY": r"disab|impair|wheelchair|autis|dyslex|udid|blind|deaf|accommodation|handicap|prosthe|locomotor",
    "PREGNANCY": r"pregnan|maternity|paternity|expecting|trimester|miscarriage|delivery|delivered|due date|baby",
    "SEXUAL_ORIENTATION": r"\bgay\b|lesbian|bisexual|transgender|non-binary|lgbt|homosexual|queer|sexual orientation|partner",
    "RELIGION": r"muslim|hindu|christian|sikh|buddh|jain|parsi|eid|church|temple|mosque|ramzan|ramadan|diwali|religio|namaz|prayer|turban|convert|fasting|puja",
    "CASTE": r"caste|dalit|scheduled|\bobc\b|\bsc\b|\bst\b|reservation|quota|brahmin|casteist",
    "POLITICAL": r"politic|party|election|rally|campaign|mla|\bmp\b|candidate|vote|panchayat",
    "UNION_MEMBERSHIP": r"union|strike|collective bargaining|\bcitu\b|\bintuc\b|workers' association|membership",
    "BIOMETRIC": r"fingerprint|retina|iris|face (?:scan|data|recognition)|thumb|biometric|palm",
    "CRIMINAL_OR_DISCIPLINARY": r"show-cause|suspen|misconduct|criminal|fir\b|convict|terminat|warning|disciplin|inquiry|arrest|charge|theft|chori|assault",
    "PERFORMANCE_RATING": r"rating|rated|appraisal|\bpip\b|kra|bell|calibrat|performer|score|improvement|exceeds|meets",
    "FINANCIAL_HARDSHIP": r"debt|loan|advance|garnish|emi|karza|hardship|financial|bankrupt|recovery|default|attached",
    "MINOR": r"minor|\bage\b|aged|years? old|saal|child|son\b|daughter|beta|guardian|intern",
}.items()}


_GATE_SPEC = GenreSpec("gate", "content gate text", allow_no_slots=True, min_chars=40, max_chars=2500)


def validate_gate_template(template: str, labels: list[str]) -> tuple[bool, str]:
    ok, why = validate_template(template, _GATE_SPEC)
    if not ok:
        return False, why
    for lab in labels:
        if lab != "NONE" and not CUES[lab].search(re.sub(r"\{\{[^}]*\}\}", " ", template)):
            return False, f"no lexical cue for {lab}"
    return True, "ok"


def build_gate_messages(labels: list[str], rng: random.Random, lang: str, n: int = 4) -> list[dict]:
    names = ", ".join(labels)
    sys = ("You write realistic but entirely fictional HR helpdesk texts for training a classifier that detects sensitive content. "
           "All personal values are replaced by typed slots.")
    if labels == ["NONE"]:
        task = ("The texts must contain NO sensitive personal attribute of any specific person: no health, disability, pregnancy, "
                "orientation, religion, caste, politics, union, biometric, criminal/disciplinary, rating, hardship or minor information. "
                "Include tricky look-alikes: generic policy mentions of these topics, and ordinary requests.")
    else:
        task = (f"Each text must disclose, about a specific person or case, exactly these kinds of sensitive content: {names}. "
                "Do not add other sensitive categories. Describe them as a real HR ticket, email, note or chat line would.")
    user = f"""Write {n} different texts of 1-4 sentences each. {task}
Voice: {rng.choice(PERSONAS)}; tone: {rng.choice(TONES)}. {LANG_MIX_NOTES.get(lang, LANG_MIX_NOTES['en'])}

{slot_help()}

Rules: never type a real-looking name, phone, id, date of birth or amount yourself: use slots. Separate texts with a line containing only {TEMPLATE_DELIM}. No numbering, no explanations."""
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def sample_label_set(rng: random.Random, none_prob: float = 0.2) -> list[str]:
    if rng.random() < none_prob:
        return ["NONE"]
    k = rng.choices((1, 2, 3), weights=(6, 3, 1))[0]
    return sorted(rng.sample(GATE_POSITIVE, k))


def gate_records_from_templates(templates, per_template: int, seed: int, generator: str | None = None) -> list[dict]:
    """Templates {"id","lang","template","labels"} -> gate records. Labels were set by the prompt and cue-checked."""
    out = []
    for t in templates:
        gen = generator or "llm:" + str(t.get("model", "unknown")) + "+slots"
        for k in range(per_template):
            text, _ = fill_template(t["template"], derive_rng(seed, t["id"], k))
            out.append(make_gate_record(f"{t['id']}-{k}", "gate", t.get("lang", "en"), text, list(t["labels"]), gen, template_id=t["id"]))
    return out


def _renumber(sentence: str, base: int) -> str:
    return re.sub(r"_(\d+)(\}\}|:)", lambda m: f"_{int(m.group(1)) + base}{m.group(2)}", sentence)


def programmatic_gate_records(n: int, seed: int) -> list[dict]:
    """Labelled gate documents from the seed sentences: 1-3 positive sentences (+ optional neutral filler) joined."""
    out = []
    for i in range(n):
        rng = derive_rng(seed, "gate", i)
        labs = sample_label_set(rng, 0.2)
        parts: list[tuple[str, str]] = []
        if labs != ["NONE"]:
            for lab in labs:
                parts.append(rng.choice(SEEDS[lab]))
        for _ in range(rng.randint(0 if parts else 1, 2)):
            parts.append(rng.choice(NEUTRAL))
        rng.shuffle(parts)
        # one entity index space per document: renumber so sentences do not accidentally share a person
        tpl, base = [], 0
        for _, s in parts:
            tpl.append(_renumber(s, base))
            base += max((int(i) for i in re.findall(r"_(\d+)(?:\}\}|:)", s)), default=0)
        lang = parts[0][0]
        text, _ = fill_template(rng.choice((" ", "\n")).join(tpl), rng)
        out.append(make_gate_record(f"gate{seed}-{i:06d}", "gate", lang, text, labs, "programmatic:gate_seeds",
                                    program_id=f"gate-{i}"))
    return out
