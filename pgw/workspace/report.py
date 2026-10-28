"""Adapter: the gateway's transparency report (`pgw_report` v1, docs/TRANSPARENCY_REPORT.md) -> the UI's view.

Everything the UI shows about a run comes from here, and everything here comes from the real gateway response:
its `pgw_report` (stages with measured milliseconds, detected spans, placeholders, the protected outbound text,
checks, outcome), its HTTP status and its X-PGW-Route header. Nothing is invented: a stage, timing or check that the
gateway did not report is not shown. A response without a report (a gateway that predates it, or an app without
`transparency: true`) yields `available: False` and only the outcome the status and route header prove.

Handled deviations from the draft spec (docs/TRANSPARENCY_REPORT.md): `outbound.messages` is null on PRIVATE_LOCAL
and in every error (`messages: None` here; hash and size kept); action "allow" (detected, crossed unchanged);
`truncated` always present; `field` on EXT_PSEUDO spans in a message `name` or tool-call arguments (kept as fields,
not drawn on the text); LOCAL_COMPUTE fields `outputs.<name>`, `instruction`, `prompt`; counts are occurrences;
`purpose`/`contract`/`route` null on early refusals; on PRIVATE_LOCAL `verify_source` is `failed` although the
request continued (the UI shows it as "kept inside", not as a stop). Streaming is not used by the workspace.

The gateway report is another team's schema. Tolerated variations are listed in the ALIASES/_STATUS tables and in
_num/_span below, so a small deviation (a renamed key, "ok" for "done", offsets as a pair) is a one-line change.
Malformed entries are dropped and counted (`dropped`), never guessed at.
"""
from __future__ import annotations

import math
import re

STAGES = ("received", "verify_source", "content_gate", "structure_check", "compute", "protect", "egress_check",
          "provider", "restore")
OUTCOMES = ("sent", "kept_inside", "refused", "error")
ROUTES = ("EXT_PSEUDO", "LOCAL_COMPUTE", "PRIVATE_LOCAL")
# Alternative spellings accepted for top-level keys and stage ids (spec name first).
ALIASES = {"egress": "egress_check", "egress_checked": "egress_check", "verify": "verify_source",
           "attestation": "verify_source", "upstream": "provider", "llm": "provider", "local_compute": "compute",
           "receive": "received", "request": "received"}
_STATUS = {"done": "done", "ok": "done", "passed": "done", "pass": "done", "skipped": "skipped", "skip": "skipped",
           "n/a": "skipped", "failed": "failed", "fail": "failed", "error": "failed", "blocked": "failed"}
_CHECK = {"pass": "pass", "passed": "pass", "ok": "pass", "fail": "fail", "failed": "fail", "n/a": "n/a",
          "na": "n/a", "not_applicable": "n/a", "skipped": "n/a"}
_ACTIONS = ("alias", "remove", "withhold", "generalize", "allow")
MAX_DETECTED = 2000
MAX_TEXT = 200_000
_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]{1,40}\]")

# Colour family of each entity type (CSS classes in static/app.css). Unknown types fall back to "other".
CATEGORY = {"PERSON": "person", "EMPLOYEE_ID": "id", "PAN": "id", "AADHAAR": "id", "AADHAAR_LIKE": "id",
            "BANK_ACCOUNT": "id", "IFSC": "id", "CARD": "id", "UPI_ID": "id", "IDENTIFIER": "id", "SECRET": "id",
            "COMPENSATION": "money", "DERIVED": "money", "DATE_OF_BIRTH": "date", "EMAIL": "contact",
            "PHONE": "contact"}
TYPE_LABEL = {"PERSON": "Name", "EMPLOYEE_ID": "Employee ID", "PAN": "PAN", "AADHAAR": "Aadhaar",
              "AADHAAR_LIKE": "Aadhaar-like number", "BANK_ACCOUNT": "Bank account", "IFSC": "IFSC code",
              "CARD": "Card number", "UPI_ID": "UPI ID", "IDENTIFIER": "Reference", "SECRET": "Secret",
              "COMPENSATION": "Pay", "DERIVED": "Computed value", "DATE_OF_BIRTH": "Date of birth", "EMAIL": "Email",
              "PHONE": "Phone"}


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return round(float(v), 3)


def _str(v, limit: int = 200) -> str | None:
    return v[:limit] if isinstance(v, str) else None


def _key(k: str) -> str:
    return ALIASES.get(k, k)


def _stages(raw) -> tuple[list[dict], int]:
    items = list(raw.items()) if isinstance(raw, dict) else [(None, s) for s in raw] if isinstance(raw, list) else []
    out, dropped, seen = [], 0, set()
    for k, s in items:
        if not isinstance(s, dict):
            dropped += 1
            continue
        sid = _key(str(s.get("id") or k or ""))
        status = _STATUS.get(str(s.get("status", "")).lower())
        if not sid or status is None or sid in seen:
            dropped += 1
            continue
        seen.add(sid)
        ms = _num(s.get("ms", s.get("duration_ms")))
        out.append({"id": sid, "status": status, "ms": ms, "known": sid in STAGES})
    out.sort(key=lambda s: STAGES.index(s["id"]) if s["known"] else len(STAGES))
    return out, dropped


def _span(d: dict, texts: dict[int, str]) -> dict | None:
    """One detected entry -> a span over the caller's original text, a payload field, or None (dropped)."""
    typ = _str(d.get("type"), 40)
    if not typ:
        return None
    action = str(d.get("action", "")).lower()
    item = {"type": typ, "category": CATEGORY.get(typ, "other"), "label": TYPE_LABEL.get(typ, typ.title()),
            "cls": _str(d.get("class", d.get("cls")), 8), "action": action if action in _ACTIONS else None,
            "placeholder": _str(d.get("placeholder"), 80), "source": _str(d.get("source"), 20)}
    if isinstance(d.get("field"), str):
        item["field"] = d["field"][:120]
        item["message"] = d.get("message") if isinstance(d.get("message"), int) else None
        return item
    start, end = d.get("start"), d.get("end")
    if "offsets" in d and isinstance(d["offsets"], list) and len(d["offsets"]) == 2:
        start, end = d["offsets"]
    msg, part = d.get("message", 0), d.get("part")
    if part is not None or not isinstance(msg, int) or isinstance(msg, bool) or msg not in texts:
        return None   # list-form content parts and other messages are not shown by this UI
    if not (isinstance(start, int) and isinstance(end, int)) or isinstance(start, bool) or isinstance(end, bool):
        return None
    if not 0 <= start < end <= len(texts[msg]):
        return None
    item.update(message=msg, start=start, end=end)
    return item


def _spans(raw, texts: dict[int, str]) -> tuple[list[dict], list[dict], int]:
    """(text spans, sorted and non-overlapping; field entries; number dropped)."""
    spans, fields, dropped = [], [], 0
    for d in (raw if isinstance(raw, list) else [])[:MAX_DETECTED]:
        s = _span(d, texts) if isinstance(d, dict) else None
        if s is None:
            dropped += 1
        elif "field" in s:
            fields.append(s)
        else:
            spans.append(s)
    spans.sort(key=lambda s: (s["message"], s["start"], -(s["end"] - s["start"])))
    kept: list[dict] = []
    for s in spans:   # overlapping spans: the earlier, longer one wins
        if kept and kept[-1]["message"] == s["message"] and s["start"] < kept[-1]["end"]:
            dropped += 1
            continue
        kept.append(s)
    return kept, fields, dropped


def _outbound(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    msgs = None   # null on PRIVATE_LOCAL and in errors: the gateway does not echo that text
    if isinstance(raw.get("messages"), list):
        msgs = [{"role": _str(m.get("role"), 20) or "user", "content": m["content"][:MAX_TEXT]}
                for m in raw["messages"] if isinstance(m, dict) and isinstance(m.get("content"), str)]
    dest = raw.get("destination")
    sha = raw.get("sha256")
    return {"destination": dest if dest in ("external", "in_house", "none") else None,
            "provider": _str(raw.get("provider"), 40), "model": _str(raw.get("model"), 80), "messages": msgs,
            "gateway_note_included": raw.get("gateway_note_included") is True,
            "sha256": sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) else None,
            "bytes": int(raw["bytes"]) if _num(raw.get("bytes")) is not None else None}


def _checks(raw) -> list[dict]:
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, dict) and isinstance(c.get("id"), str):
            out.append({"id": c["id"][:60], "status": _CHECK.get(str(c.get("status", "")).lower(), "unknown"),
                        "label": _str(c.get("label"), 400) or c["id"][:60]})
    return out


def _counts(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    by = raw.get("by_type") if isinstance(raw.get("by_type"), dict) else {}
    return {"replaced": int(raw["replaced"]) if _num(raw.get("replaced")) is not None else None,
            "removed": int(raw["removed"]) if _num(raw.get("removed")) is not None else None,
            "by_type": {str(k)[:40]: int(v) for k, v in by.items() if _num(v) is not None}}


def fallback_outcome(http_status: int, route_header: str | None, error_type: str | None) -> str:
    """The outcome a response proves without a report."""
    if http_status == 200:
        return "kept_inside" if route_header == "PRIVATE_LOCAL" else "sent"
    if error_type in ("upstream_error", "internal_error", "restore_unavailable", "private_upstream_error",
                      "gateway_timeout", "gateway_unavailable", "bad_gateway_response"):
        return "error"
    return "refused"


def build_view(report, *, http_status: int, route_header: str | None = None, error_type: str | None = None,
               texts: dict[int, str] | None = None) -> dict:
    """The UI view of one gateway response. `texts`: {message index: original content} of the messages the user
    wrote (offsets are validated against them)."""
    texts = texts or {}
    if not isinstance(report, dict):
        return {"available": False, "outcome": fallback_outcome(http_status, route_header, error_type),
                "route": "PRIVATE_LOCAL" if route_header == "PRIVATE_LOCAL" else None, "stages": [], "spans": [],
                "fields": [], "checks": [], "outbound": None, "counts": _counts(None), "restore": None,
                "total_ms": None, "dropped": 0}
    r = {_key(k): v for k, v in report.items()}
    stages, d1 = _stages(r.get("stages"))
    spans, fields, d2 = _spans(r.get("detected"), texts)
    outcome = r.get("outcome") if r.get("outcome") in OUTCOMES else fallback_outcome(http_status, route_header,
                                                                                       error_type)
    route = r.get("route") if r.get("route") in ROUTES else ("PRIVATE_LOCAL" if route_header == "PRIVATE_LOCAL"
                                                              else None)
    src = r.get("input_source") if isinstance(r.get("input_source"), dict) else {}
    rest = r.get("restore") if isinstance(r.get("restore"), dict) else None
    err = r.get("error") if isinstance(r.get("error"), dict) else None
    timed = [s["ms"] for s in stages if s["ms"] is not None]
    return {
        "available": True, "version": r.get("version") if isinstance(r.get("version"), int) else None,
        "request_id": _str(r.get("request_id"), 64), "purpose": _str(r.get("purpose"), 64),
        "contract": _str(r.get("contract"), 80), "route": route, "outcome": outcome,
        "input_source": {"id": _str(src.get("id"), 80), "verified": src.get("verified") is True},
        "stages": stages, "total_ms": round(sum(timed), 3) if timed else None,
        "spans": spans, "fields": fields, "counts": _counts(r.get("counts")),
        "outbound": _outbound(r.get("outbound")),
        "restore": {"restored": _num(rest.get("restored")), "kept_withheld": _num(rest.get("kept_withheld"))}
        if rest else None,
        "checks": _checks(r.get("checks")),
        "error": {"type": _str(err.get("type"), 60), "message": _str(err.get("message"), 300)} if err else None,
        "truncated": r.get("truncated") is True, "dropped": d1 + d2,
    }


def placeholders(text: str) -> list[str]:
    """Placeholder-shaped tokens in outbound text (used by tests; the UI applies the same pattern)."""
    return _PLACEHOLDER.findall(text or "")


# --- plain-language errors ------------------------------------------------------------------------------------------
# (title, what happened in plain words, whether the request reached an AI: True / False / None = unknown).
# False only for errors the gateway raises before any send (pgw/gateway/app.py).
ERRORS: dict[str, tuple[str, str, bool | None]] = {
    "invalid_declared_subjects": ("Stopped: people missing",
                                  "This task needs to know who the text is about. Add the required person under "
                                  "“People in this request” and run it again.", False),
    "declared_subjects_not_supported": ("Stopped", "This task does not take a list of people.", False),
    "input_not_source_sanitized": ("Stopped: source not verified",
                                   "This task only accepts input signed by an approved source system.", False),
    "invalid_contract_payload": ("Stopped: form not accepted",
                                 "The gateway did not accept the form values for this task.", False),
    "request_blocked": ("Stopped on purpose",
                        "The text contains details that must never be sent to an AI.", False),
    "special_category_content": ("Stopped on purpose",
                                 "The text is about a topic this task keeps inside the company.", False),
    "content_gate_unavailable": ("Stopped: check unavailable",
                                 "The sensitive-topic check could not run, so nothing was sent.", False),
    "structured_data_in_free_text": ("Stopped: table or export in the text",
                                     "The text contains a table, an export or data records. Records are not sent "
                                     "to an external AI as free text; use the records form for this task or the "
                                     "in-house route.", False),
    "structure_check_unavailable": ("Stopped: check unavailable",
                                    "The check for tables and exports could not run, so nothing was sent.", False),
    "content_requires_private_route": ("Stopped on purpose",
                                       "Part of the text is in a script the gateway cannot check reliably.", False),
    "sensitive_data_in_parameters": ("Stopped on purpose", "The request carried personal details where none are "
                                     "allowed.", False),
    "request_too_large": ("Too long", "This is longer than the task allows. Shorten it and try again.", False),
    "content_too_large": ("Too long", "This is longer than the task allows. Shorten it and try again.", False),
    "PGW_EGRESS_VIOLATION": ("Stopped by the final check",
                             "The last check before sending found personal details in the outgoing text, so the "
                             "request was stopped.", False),
    "gateway_busy": ("Gateway busy", "The privacy gateway is at capacity. Try again in a moment.", False),
    "protection_timeout": ("Stopped: took too long", "Protecting this text took too long, so it was not sent. "
                           "Try a shorter text.", False),
    "detector_unavailable": ("Protection unavailable", "A protection component is unavailable, so the gateway "
                             "sent nothing. Try again shortly.", False),
    "ledger_unavailable": ("Audit log unavailable", "The gateway could not write its audit record, so it sent "
                           "nothing. Try again shortly.", False),
    "replay_store_unavailable": ("Gateway unavailable", "A gateway safety store is unavailable, so it sent nothing. "
                                 "Try again shortly.", False),
    "compute_unavailable": ("Computation failed", "The calculation inside the gateway failed, so nothing was "
                            "sent.", False),
    "upstream_error": ("No answer from the AI", "The request left protected, but the AI service did not answer "
                       "properly. No answer was returned.", True),
    "private_upstream_error": ("In-house model unavailable", "The in-house model did not answer. Nothing was sent "
                               "outside the company.", False),
    "private_model_unavailable": ("In-house model unavailable", "The in-house model is not available right now. "
                                  "Nothing was sent outside the company.", False),
    "restore_unavailable": ("Answer withheld", "The answer was withheld because the real details could not be put "
                            "back safely.", None),
    "invalid_request": ("Request not accepted", "The gateway could not read this request. Reload the page and "
                        "try again; if it keeps happening, contact IT.", False),
    "invalid_parameter": ("Request not accepted", "The gateway did not accept a setting of this request. Contact "
                          "IT.", False),
    "unsupported_parameter": ("Request not accepted", "The gateway did not accept a setting of this request. "
                              "Contact IT.", False),
    "invalid_conversation_id": ("Request not accepted", "The gateway did not accept this request.", False),
    "purpose_required": ("Request not accepted", "The workspace did not name a task. Contact IT.", False),
    "unauthorized": ("Workspace not authorised", "The workspace is not authorised at the privacy gateway. Please "
                     "contact your administrator.", False),
    "purpose_forbidden": ("Task not enabled", "This task is not enabled for the workspace at the gateway.", False),
    "no_data_contract": ("Task not enabled", "This task has no data contract at the gateway.", False),
    "model_forbidden": ("Model not allowed", "The configured AI model is not allowed for the workspace.", False),
    "internal_error": ("Gateway error", "The privacy gateway hit an internal error. No answer was returned.", None),
    "gateway_timeout": ("Gateway did not answer in time", "The privacy gateway took too long to answer. Try again; "
                        "if it keeps happening, contact IT.", None),
    "gateway_unavailable": ("Cannot reach the privacy gateway", "The workspace could not reach the privacy "
                            "gateway, so your request was not processed. Try again; if it keeps happening, contact "
                            "IT.", False),
    "bad_gateway_response": ("Unexpected gateway response", "The privacy gateway sent a response the workspace "
                             "could not read. No answer is shown.", None),
}
_DEFAULT = ("Stopped", "The privacy gateway did not accept this request.", False)


def plain_error(etype: str | None, gateway_message: str | None = None) -> dict:
    title, text, sent = ERRORS.get(etype or "", _DEFAULT)
    return {"type": (etype or "unknown")[:60], "title": title, "plain": text, "sent": sent,
            # gateway error messages are content-free by design (ADR-0007); shown as the technical detail
            "detail": gateway_message[:300] if isinstance(gateway_message, str) else None}
