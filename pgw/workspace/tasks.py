"""The HR Workspace task catalogue: what staff can ask for, and how each request is built.

Two kinds of task:

* Free text (ticket summary, HR case explanation, email draft). Staff paste arbitrary text, so the workspace is NOT a
  source-side sanitizer and never signs it: the request goes to the gateway unattested and the contract keeps it
  inside (in-house model). "People in this request" fills `pgw_subjects` with the roles the contract knows.
* Forms (salary comparison, ESI eligibility, pay-band position). Every value is a typed, validated field and the
  instruction is one of a fixed list, so the payload the workspace builds contains nothing a person typed freely. Only
  for these does the workspace act as the source and sign (`FORM_SOURCE_ID`, its own key).

The schemas here mirror the contracts' `input_schema` (tests/test_workspace.py checks they agree). Error messages
name a field path, never a value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FORM_SOURCE_ID = "hr-workspace-forms@1"
SUBJECT_FIELDS = ("name", "employee_id", "email", "phone")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_EMP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/]{2,19}")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{1,60}")
_PHONE = re.compile(r"\+?[0-9][0-9 ()\-]{5,22}")
SUBJECT_MAX = {"name": 100, "employee_id": 20, "email": 254, "phone": 24}


class InputError(ValueError):
    """The request from the browser does not fit the task. `path` names the field; messages never contain values."""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path, self.message = path, message


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    max: int
    required: bool = False


@dataclass(frozen=True)
class Field:
    path: str              # e.g. "employees[0].annual_ctc": the payload path the gateway report refers to
    label: str
    kind: str              # name | employee_id | amount | band
    min: int = 0
    max: int = 0
    hint: str = ""
    group: str = ""


@dataclass(frozen=True)
class Choice:
    id: str
    text: str


@dataclass(frozen=True)
class Task:
    id: str
    purpose: str
    kind: str                       # "text" | "form"
    title: str
    summary: str
    route_note: str
    max_chars: int = 0
    input_label: str = ""
    placeholder: str = ""
    instruction: str = ""           # free text: the fixed task instruction (system message)
    roles: tuple[Role, ...] = ()
    fields: tuple[Field, ...] = ()
    choices: tuple[Choice, ...] = ()
    bands: tuple[tuple[str, int, int], ...] = ()
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        """What the browser needs to render the task (no secrets, nothing request-specific)."""
        d = {"id": self.id, "purpose": self.purpose, "kind": self.kind, "title": self.title, "summary": self.summary,
             "route_note": self.route_note}
        if self.kind == "text":
            d.update(max_chars=self.max_chars, input_label=self.input_label, placeholder=self.placeholder,
                     roles=[{"id": r.id, "label": r.label, "max": r.max, "required": r.required} for r in self.roles],
                     subject_fields=list(SUBJECT_FIELDS))
        else:
            d.update(fields=[{"path": f.path, "label": f.label, "kind": f.kind, "min": f.min, "max": f.max,
                              "hint": f.hint, "group": f.group} for f in self.fields],
                     choices=[{"id": c.id, "text": c.text} for c in self.choices],
                     bands=[{"id": b, "min": lo, "max": hi} for b, lo, hi in self.bands])
        return d


_KEPT_INSIDE = ("Kept inside: handled by the in-house model. Pasted text is not signed by an approved source "
                "system, so the gateway keeps it within the company.")
_COMPUTED = ("Computed inside the gateway: exact pay stays inside; only placeholders and the computed result reach "
             "the external AI.")
_PEOPLE = (Role("requester", "Requester", 1, True), Role("assignee", "Assignee", 1),
           Role("mentioned", "Mentioned", 20), Role("other", "Other person", 10))

TASKS: dict[str, Task] = {t.id: t for t in (
    Task("ticket_summary", "ticket_summary", "text", "Summarise a ticket",
         "A three-line summary of an HR helpdesk ticket.", _KEPT_INSIDE, 20000, "Ticket text",
         "Paste the ticket, including its comments", instruction=(
             "Summarise this HR helpdesk ticket in three short lines: the issue, what was done, and what happens "
             "next. Use only what the ticket states."), roles=_PEOPLE),
    Task("hr_case_explanation", "hr_case_explanation", "text", "Explain an HR case",
         "Facts, possible causes to verify and next steps for an HR case.", _KEPT_INSIDE, 20000, "Case text",
         "Paste the case notes", instruction=(
             "Explain this HR case for HR Operations: the facts it states, possible causes to verify, and the next "
             "checks to make. Use only what the case states."), roles=_PEOPLE),
    Task("hr_email_draft", "hr_email_draft", "text", "Draft an email",
         "A short, professional email from HR Operations.", _KEPT_INSIDE, 8000, "What the email should say",
         "Paste your notes for the email", instruction=(
             "Draft a short, professional email from HR Operations based on these notes. Use only what the notes "
             "state."), roles=(Role("recipient", "Recipient", 1, True), Role("mentioned", "Mentioned", 10),
                               Role("other", "Other person", 5))),
    Task("salary_comparison", "salary_comparison", "form", "Compare two salaries",
         "Who earns more, and by how much, without the AI seeing either salary.", _COMPUTED,
         fields=(Field("employees[0].name", "Name", "name", group="Employee A"),
                 Field("employees[0].employee_id", "Employee ID", "employee_id", group="Employee A"),
                 Field("employees[0].annual_ctc", "Annual CTC", "amount", 1, 1_000_000_000, "In rupees, per year",
                       "Employee A"),
                 Field("employees[1].name", "Name", "name", group="Employee B"),
                 Field("employees[1].employee_id", "Employee ID", "employee_id", group="Employee B"),
                 Field("employees[1].annual_ctc", "Annual CTC", "amount", 1, 1_000_000_000, "In rupees, per year",
                       "Employee B")),
         choices=(Choice("who_more", "Who earns more, and by how much? Write a two-line note for the HR manager."),
                  Choice("neutral", "Summarise the pay difference between these two employees in one neutral "
                                    "sentence."),
                  Choice("review", "Write a short note for a compensation review about the gap between these two "
                                   "employees."))),
    Task("esi_letter", "esi_letter", "form", "ESI eligibility",
         "A short explanation for an employee of whether they are covered by ESI.", _COMPUTED,
         fields=(Field("employee[0].name", "Name", "name", group="Employee"),
                 Field("employee[0].employee_id", "Employee ID", "employee_id", group="Employee"),
                 Field("employee[0].monthly_gross", "Monthly gross wage", "amount", 1, 100_000_000,
                       "In rupees, per month", "Employee")),
         choices=(Choice("letter", "Write a short, friendly letter explaining to the employee whether they are "
                                   "covered by ESI."),
                  Choice("answer", "Answer the employee's question about their ESI coverage in plain words, in "
                                   "three sentences or fewer."))),
    Task("band_position", "band_position", "form", "Pay-band position",
         "Where an employee sits in their pay band, without the AI seeing their pay.", _COMPUTED,
         fields=(Field("employee[0].name", "Name", "name", group="Employee"),
                 Field("employee[0].employee_id", "Employee ID", "employee_id", group="Employee"),
                 Field("employee[0].annual_ctc", "Annual CTC", "amount", 1, 1_000_000_000, "In rupees, per year",
                       "Employee"),
                 Field("band[0].name", "Pay band", "band", group="Band")),
         choices=(Choice("manager", "Explain where this employee sits in their pay band, in two sentences for the "
                                    "HR manager."),
                  Choice("review", "Write a short note for a pay review about this employee's position in the "
                                   "band.")),
         bands=(("P1", 600000, 1000000), ("P2", 1000000, 1800000), ("P3", 1800000, 2600000),
                ("P4", 2600000, 4000000))),
)}


# --- validation --------------------------------------------------------------------------------------------------

def _line(v, path: str, max_len: int) -> str:
    if not isinstance(v, str):
        raise InputError(path, "expected text")
    v = v.strip()
    if not v:
        raise InputError(path, "required")
    if len(v) > max_len or _CTRL.search(v):
        raise InputError(path, f"must be a single line of at most {max_len} characters")
    return v


def free_text(task: Task, text) -> str:
    """The pasted text, exactly as typed (offsets in the gateway report refer to it)."""
    if not isinstance(text, str) or not text.strip():
        raise InputError("text", "paste some text first")
    if len(text) > task.max_chars:
        raise InputError("text", f"longer than this task allows ({task.max_chars} characters)")
    if "\x00" in text:
        raise InputError("text", "contains characters that cannot be sent")
    return text


def subjects(task: Task, people) -> dict | None:
    """`pgw_subjects` from the "People in this request" rows: [{role, name, employee_id, email, phone}, ...].
    Shape and format are checked here; whether the contract's required roles are present is the gateway's decision
    (its refusal is shown to the user)."""
    if people in (None, []):
        return None
    if not isinstance(people, list) or len(people) > 40:
        raise InputError("people", "expected a list of people")
    roles = {r.id: r for r in task.roles}
    out: dict[str, list] = {}
    for i, p in enumerate(people):
        path = f"people[{i}]"
        if not isinstance(p, dict) or set(p) - {"role", *SUBJECT_FIELDS}:
            raise InputError(path, "unexpected fields")
        role = p.get("role")
        if role not in roles:
            raise InputError(f"{path}.role", "not a role this task accepts")
        subj = {}
        for f in SUBJECT_FIELDS:
            v = p.get(f)
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            subj[f] = _line(v, f"{path}.{f}", SUBJECT_MAX[f])
        if not subj:
            continue
        if "employee_id" in subj and not _EMP_ID.fullmatch(subj["employee_id"]):
            raise InputError(f"{path}.employee_id", "letters, digits and - _ / only")
        if "email" in subj and not _EMAIL.fullmatch(subj["email"]):
            raise InputError(f"{path}.email", "not an email address")
        if "phone" in subj and not _PHONE.fullmatch(subj["phone"]):
            raise InputError(f"{path}.phone", "not a phone number")
        if "name" in subj and len(re.sub(r"[\W\d_]", "", subj["name"])) < 2:
            raise InputError(f"{path}.name", "not a usable name")
        out.setdefault(role, []).append(subj)
        if len(out[role]) > roles[role].max:
            raise InputError(f"{path}.role", f"at most {roles[role].max} for this role")
    return out or None


def _set(payload: dict, path: str, value) -> None:
    """Assign `value` at a path like "employees[1].annual_ctc"."""
    head, leaf = path.split(".")
    key, idx = re.fullmatch(r"(\w+)\[(\d+)\]", head).groups()
    lst = payload.setdefault(key, [])
    while len(lst) <= int(idx):
        lst.append({})
    lst[int(idx)][leaf] = value


def form_payload(task: Task, form) -> tuple[dict, list[dict]]:
    """The LOCAL_COMPUTE payload for a form task, and display rows [{path, label, group, value}] for the UI.
    Raises InputError naming the first bad field."""
    if not isinstance(form, dict):
        raise InputError("form", "expected the form fields")
    allowed = {f.path for f in task.fields} | {"instruction"}
    if set(form) - allowed:
        raise InputError("form", "unexpected fields")
    payload: dict = {}
    rows = []
    for f in task.fields:
        v = form.get(f.path)
        if f.kind == "name":
            v = _line(v, f.path, 100)
            if len(re.sub(r"[\W\d_]", "", v)) < 2:
                raise InputError(f.path, "not a usable name")
        elif f.kind == "employee_id":
            v = _line(v, f.path, 20)
            if not _EMP_ID.fullmatch(v):
                raise InputError(f.path, "letters, digits and - _ / only (3 to 20)")
        elif f.kind == "amount":
            if isinstance(v, str) and re.fullmatch(r"\s*[0-9][0-9,]*\s*", v):
                v = int(v.replace(",", ""))
            if isinstance(v, bool) or not isinstance(v, int):
                raise InputError(f.path, "expected a whole number of rupees")
            if not f.min <= v <= f.max:
                raise InputError(f.path, "outside the allowed range")
        elif f.kind == "band":
            if v not in {b for b, _lo, _hi in task.bands}:
                raise InputError(f.path, "choose a pay band")
        _set(payload, f.path, v)
        rows.append({"path": f.path, "label": f.label, "group": f.group, "kind": f.kind, "value": v})
    choice = {c.id: c.text for c in task.choices}.get(form.get("instruction"))
    if choice is None:
        raise InputError("instruction", "choose one of the listed instructions")
    payload = {"instruction": choice, **payload}
    rows.insert(0, {"path": "instruction", "label": "Instruction", "group": "", "kind": "choice", "value": choice})
    return payload, rows
