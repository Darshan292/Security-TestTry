"""Genre specifications: what a real Indian HR helpdesk sees, with emphasis on layouts where regex detectors failed
(fixed-width exports, CSV/TSV pastes, quoted email threads, OCR text, code-mixed chat, kinship mentions).

A `GenreSpec` drives two things: the LLM prompt for template writing (synth.build_messages) and the sampling of
diversity controls. `program` names a purely programmatic generator in structured.py when one exists, so the
pipeline can run with no GPU.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GenreSpec:
    name: str
    description: str                       # what the LLM is asked to write
    langs: tuple[str, ...] = ("en",)
    typical_slots: tuple[str, ...] = ()    # slot types the genre usually contains
    layout: str = "prose"                  # prose | fixed_width | csv | tsv | markdown_table | form | chat | email | list
    program: str | None = None             # name of the no-LLM generator in structured.py
    weight: float = 1.0
    gate_hint: tuple[str, ...] = ()        # content-gate labels that naturally occur in this genre
    min_chars: int = 200
    max_chars: int = 3000
    notes: str = ""                        # extra instruction for the writer
    allow_no_slots: bool = False


G = GenreSpec
GENRES: tuple[GenreSpec, ...] = (
    G("payroll_fixed_width", "A fixed-width payroll export: a title line, a header line with column names, a ruler or dashes "
      "line, then 4-12 employee rows whose columns are aligned with spaces, then a totals line. Use one slot per cell.",
      typical_slots=("EMPLOYEE_ID", "PERSON", "PAN", "BANK_ACCOUNT", "IFSC", "COMPENSATION"), layout="fixed_width",
      program="payroll_fixed", weight=2.0, min_chars=500),
    G("csv_paste", "Rows pasted from a CSV export of an HRIS (header row, then 3-10 rows, comma separated, some quoted).",
      typical_slots=("EMPLOYEE_ID", "PERSON", "EMAIL", "PHONE", "DATE_OF_BIRTH", "COMPENSATION"), layout="csv",
      program="csv_export", weight=2.0),
    G("tsv_paste", "Tab-separated rows copied from a spreadsheet into a ticket, with a header row.",
      typical_slots=("EMPLOYEE_ID", "PERSON", "BANK_ACCOUNT", "IFSC", "PAN"), layout="tsv", program="tsv_paste"),
    G("markdown_table", "A markdown table (pipes and a header separator row) summarising several employees or a salary "
      "revision, with a sentence before and after.", typical_slots=("PERSON", "EMPLOYEE_ID", "COMPENSATION"),
      layout="markdown_table", program="markdown_table"),
    G("salary_slip", "A monthly salary slip: company header, employee block (name, id, PAN, bank, UAN), earnings and deductions "
      "tables with amounts, net pay, and a computer-generated footer.",
      typical_slots=("PERSON", "EMPLOYEE_ID", "PAN", "BANK_ACCOUNT", "COMPENSATION", "IDENTIFIER"), layout="form",
      program="salary_slip", weight=2.0, min_chars=600),
    G("form16_extract", "An extract of Form 16 Part A/B text as it comes out of a PDF: TAN, PAN, assessment year, gross salary, "
      "section 80C/80D deductions (statutory limits are NOT personal), tax payable.",
      typical_slots=("PERSON", "PAN", "COMPENSATION"), layout="form", program="form16_extract", min_chars=500),
    G("email_thread", "An email thread with headers (From/To/Subject/Date), a reply, a quoted earlier message starting with '>' "
      "lines, and a signature block with name, designation, phone and email.",
      typical_slots=("PERSON", "EMAIL", "PHONE", "EMPLOYEE_ID"), layout="email", program="email_thread", weight=2.0, min_chars=500),
    G("whatsapp_chat", "A WhatsApp-style chat export ('12/03/24, 10:41 pm - Name: message') between an employee and an HR "
      "executive, informal, with typos, in Hinglish or English.", langs=("en", "hi-Latn", "en-hi"),
      typical_slots=("PERSON", "PHONE", "BANK_ACCOUNT", "UPI_ID", "AADHAAR"), layout="chat", program="chat_log", weight=2.0),
    G("slack_log", "A Slack channel excerpt (#hr-helpdesk) with usernames, timestamps and short messages, one person pasting "
      "a screenshot transcription.", typical_slots=("PERSON", "EMPLOYEE_ID", "EMAIL", "SECRET"), layout="chat"),
    G("hinglish_message", "A message to HR written in Hinglish (Hindi in Latin script mixed with English words).",
      langs=("hi-Latn", "en-hi"), typical_slots=("PERSON", "PHONE", "EMPLOYEE_ID", "COMPENSATION"), weight=1.5),
    G("regional_latin_message", "A message to HR where Tamil, Telugu, Kannada, Malayalam, Bengali or Marathi is written in Latin "
      "script mixed with English, naming people the regional way (initials, patronymics).",
      langs=("ta-Latn", "te-Latn", "kn-Latn", "ml-Latn", "bn-Latn", "mr-Latn"), typical_slots=("PERSON", "PHONE", "COMPENSATION")),
    G("ocr_noisy_scan", "Text as it comes out of a poor OCR of a scanned HR form: broken lines, O/0 and l/1 confusions, stray "
      "symbols, doubled spaces. Keep every slot intact; noise goes in the surrounding words.",
      typical_slots=("PERSON", "PAN", "AADHAAR", "DATE_OF_BIRTH", "BANK_ACCOUNT"), layout="form", program="ocr_form", weight=1.5),
    G("bullet_list", "A bullet or numbered list of facts about one employee or a small case, as an HR executive would jot down.",
      typical_slots=("PERSON", "EMPLOYEE_ID", "PHONE", "COMPENSATION", "DATE_OF_BIRTH"), layout="list"),
    G("address_block", "A multi-line postal address inside a form or letter (house no, street, locality, city, state, PIN), "
      "next to a person's name and phone. The address itself is not a labelled entity; its place names are hard negatives.",
      typical_slots=("PERSON", "PHONE"), layout="form", program="address_block"),
    G("kinship_mention", "A short note where an employee mentions family: 'my mother Sunita', 'wife', 'father-in-law', nominee "
      "details for insurance or PF, dependants' names and dates of birth.", typical_slots=("PERSON", "DATE_OF_BIRTH", "PHONE"),
      gate_hint=("MINOR", "HEALTH")),
    G("offer_letter", "A formal offer letter: candidate name and address, designation, CTC breakup, joining date, reporting "
      "manager, acceptance block.", typical_slots=("PERSON", "COMPENSATION", "EMAIL", "PHONE"), min_chars=600, max_chars=3500),
    G("relieving_letter", "A relieving / experience letter with employee id, tenure dates (not DOB), designation and last drawn "
      "salary.", typical_slots=("PERSON", "EMPLOYEE_ID", "COMPENSATION")),
    G("warning_letter", "A warning or show-cause letter about attendance, conduct or performance, naming the employee, a manager "
      "and a policy clause.", typical_slots=("PERSON", "EMPLOYEE_ID"), gate_hint=("CRIMINAL_OR_DISCIPLINARY", "PERFORMANCE_RATING")),
    G("leave_request", "A leave application email or form (casual, earned, comp-off) with dates, reason, handover person, contact "
      "number during leave.", typical_slots=("PERSON", "EMPLOYEE_ID", "PHONE"), gate_hint=("HEALTH",)),
    G("medical_leave_request", "A medical leave request with a doctor's note summary, diagnosis or procedure, dates and a "
      "request for confidentiality.", typical_slots=("PERSON", "EMPLOYEE_ID", "PHONE"),
      gate_hint=("HEALTH", "DISABILITY", "PREGNANCY")),
    G("grievance_ticket", "A helpdesk grievance ticket (portal export fields: ticket no, category, priority, description) about "
      "payroll error, harassment complaint, transfer, appraisal dispute.", typical_slots=("PERSON", "EMPLOYEE_ID", "EMAIL"),
      gate_hint=("CRIMINAL_OR_DISCIPLINARY", "PERFORMANCE_RATING", "CASTE", "RELIGION", "SEXUAL_ORIENTATION", "UNION_MEMBERSHIP"),
      weight=1.5),
    G("bank_change_request", "A request to change salary bank account: old and new account numbers, IFSC, cancelled cheque "
      "mention, employee id, approval chain.", typical_slots=("PERSON", "EMPLOYEE_ID", "BANK_ACCOUNT", "IFSC", "PAN"),
      program="bank_change_form", weight=1.5),
    G("reimbursement_claim", "An expense or medical reimbursement claim with bill numbers (not personal), amounts claimed, UPI "
      "or bank details for payout.", typical_slots=("PERSON", "EMPLOYEE_ID", "COMPENSATION", "UPI_ID", "BANK_ACCOUNT")),
    G("bgv_form", "A background-verification form: full name, DOB, PAN, Aadhaar, passport/DL, previous employer, referees with "
      "phone numbers, address history.", typical_slots=("PERSON", "DATE_OF_BIRTH", "PAN", "AADHAAR", "IDENTIFIER", "PHONE", "EMAIL"),
      layout="form", program="bgv_form", weight=1.5, gate_hint=("CRIMINAL_OR_DISCIPLINARY",)),
    G("onboarding_checklist", "An onboarding checklist for a new joiner with documents collected (Aadhaar copy, PAN, cancelled "
      "cheque) and their numbers.", typical_slots=("PERSON", "AADHAAR", "PAN", "BANK_ACCOUNT", "IFSC", "EMPLOYEE_ID"), layout="list"),
    G("it_access_ticket", "An IT access or password reset request where credentials, API keys or tokens get pasted by mistake.",
      typical_slots=("PERSON", "EMPLOYEE_ID", "EMAIL", "SECRET"), weight=1.2),
    G("appraisal_note", "A manager's appraisal or promotion note with rating, hike percentage, current and proposed pay.",
      typical_slots=("PERSON", "EMPLOYEE_ID", "COMPENSATION"), gate_hint=("PERFORMANCE_RATING",)),
    G("nomination_form", "A PF / gratuity / insurance nomination form with nominee names, relationship, share percent, DOB and "
      "guardian details for minors.", typical_slots=("PERSON", "DATE_OF_BIRTH", "AADHAAR", "BANK_ACCOUNT"), layout="form",
      gate_hint=("MINOR",)),
    G("policy_faq_negative", "A policy question or circular with NO personal data: statutory limits, helpline numbers, holiday "
      "list, form numbers, role mailboxes, place names. Use negative slots only.", layout="prose", allow_no_slots=True,
      weight=1.5, notes="This genre must contain zero person slots; it exists to teach what is NOT sensitive."),
    G("placement_verification", "A third-party employment verification request or reply (bank/embassy asks HR to confirm "
      "designation and salary of an employee).", typical_slots=("PERSON", "EMPLOYEE_ID", "COMPENSATION", "EMAIL")),
    G("exit_fnf", "A full-and-final settlement statement: last working day, notice recovery, leave encashment, gratuity, net payable, "
      "bank credit details.", typical_slots=("PERSON", "EMPLOYEE_ID", "COMPENSATION", "BANK_ACCOUNT", "IFSC"), layout="form"),
    G("json_payload", "A JSON snippet from an HRIS API or webhook (employee record) pasted into a ticket, possibly truncated.",
      typical_slots=("EMPLOYEE_ID", "PERSON", "EMAIL", "PHONE", "COMPENSATION", "DATE_OF_BIRTH", "PAN"), layout="form"),
    G("union_political_note", "A sensitive HR note touching on union activity, political leaning, religion or caste in a "
      "workplace dispute, describing it neutrally.", typical_slots=("PERSON", "EMPLOYEE_ID"),
      gate_hint=("UNION_MEMBERSHIP", "POLITICAL", "RELIGION", "CASTE")),
)
BY_NAME = {g.name: g for g in GENRES}
assert len(BY_NAME) == len(GENRES)

PERSONAS = ("a payroll executive at a mid-size IT services firm", "an HR business partner at a manufacturing company",
            "a new joiner who is not comfortable with English", "a frontline employee at a retail chain", "a finance controller",
            "a recruiter at a staffing agency", "an HR helpdesk agent working night shifts", "a manager in a logistics company",
            "a senior employee writing to HR angrily", "an intern at a startup", "a compliance officer at a bank",
            "an employee on a factory floor using a phone keyboard", "an HR head of a hospital group", "a payroll vendor's analyst")
TONES = ("formal", "terse", "polite", "frustrated", "urgent", "casual", "bureaucratic", "apologetic", "neutral")
LENGTHS = {"short": (200, 600), "medium": (600, 1500), "long": (1500, 3000)}
LANG_MIX_NOTES = {
    "en": "Write in Indian English.",
    "hi-Latn": "Write Hindi in Latin script (Hinglish), mixing English nouns naturally.",
    "en-hi": "Write mostly English with Hindi words and phrases in Latin script mixed in.",
    "ta-Latn": "Write English mixed with Tamil words in Latin script.",
    "te-Latn": "Write English mixed with Telugu words in Latin script.",
    "kn-Latn": "Write English mixed with Kannada words in Latin script.",
    "ml-Latn": "Write English mixed with Malayalam words in Latin script.",
    "bn-Latn": "Write English mixed with Bengali words in Latin script.",
    "mr-Latn": "Write English mixed with Marathi words in Latin script.",
}


@dataclass
class Controls:
    persona: str
    tone: str
    length: str
    lang: str
    temperature: float
    extra_negatives: tuple[str, ...] = field(default_factory=tuple)


def sample_controls(rng: random.Random, spec: GenreSpec) -> Controls:
    length = rng.choice(tuple(LENGTHS))
    if spec.min_chars >= 500 and length == "short":
        length = "medium"
    return Controls(persona=rng.choice(PERSONAS), tone=rng.choice(TONES), length=length, lang=rng.choice(spec.langs),
                    temperature=round(rng.uniform(0.7, 1.1), 2),
                    extra_negatives=tuple(sorted(rng.sample(("TICKET", "STATUTORY", "INVOICE", "EXT", "HELPLINE", "DATE", "PIN",
                                                              "COUNT", "ROLE_EMAIL", "PLACE", "NAMEDPLACE", "ORG", "NAMEWORD"),
                                                             k=rng.randint(1, 3)))))


def sample_genre(rng: random.Random, allow: tuple[str, ...] | None = None) -> GenreSpec:
    pool = [g for g in GENRES if allow is None or g.name in allow]
    return rng.choices(pool, weights=[g.weight for g in pool], k=1)[0]
