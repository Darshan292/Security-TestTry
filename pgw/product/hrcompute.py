"""Deterministic local computations over HRMS records (docs/HRMS_CONTRACTS.md).

Each computation takes the projected rows of a record contract (only the fields the contract SELECTS, already typed
and classified by the field schema), the contract's validated params and its sealed rules file, and returns named
outputs of a declared kind. Exact values stay here: what leaves is decided by the contract's per-output egress and
rendered by pgw.product.records (counts, fixed exception codes, sealed band labels, aliased identities).

Statutory values (PF/ESIC/PT/TDS rates, ceilings, slabs; working-hour limits) are NOT constants in this code: they
come from the signed rules files in registry/rules/, each value with its source cited there and a "verify current
rates" note. A missing or malformed rule stops startup.

A required input that is missing or does not parse is an explicit per-row code (MISSING:<field>, INVALID:<field>);
checks that depend on it are skipped and the row is counted as incomplete. Nothing passes silently.

Output kinds:
  count   int                       may leave RAW
  codes   {code: count}             may leave RAW (codes are a fixed vocabulary)
  label   str (fixed vocabulary)    may leave RAW
  list    [(row, tokens)]           may leave RAW: each row is named by its aliased identities, tokens are codes/bands
  groups  [(KEEP value, tokens)]    may leave RAW
  amount  Decimal                   INTERNAL, or TRANSFORMED as a band with sealed edges; never RAW
  percent Decimal                   INTERNAL, or TRANSFORMED as a band with sealed edges; never RAW
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .fieldschema import SchemaError, band_labels, parse_edges

KINDS = ("count", "codes", "label", "list", "groups", "amount", "percent")
LEAVES_RAW = frozenset({"count", "codes", "label", "list", "groups"})
BANDED = frozenset({"amount", "percent"})


@dataclass
class Row:
    index: int                                   # 1-based position in the input (non-personal)
    values: dict = field(default_factory=dict)   # selected name -> typed value (None if missing/invalid/dropped)
    errors: dict = field(default_factory=dict)   # selected name -> "MISSING" | "INVALID"


@dataclass
class Out:
    kind: str
    value: object
    meta: dict = field(default_factory=dict)   # disclosure control inputs (pgw.product.disclosure); never rendered


class ParamError(ValueError):
    """Contract params are invalid. Messages never contain values."""


def _num(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(x)


def _dec(raw, where: str, lo=None, hi=None) -> Decimal:
    try:
        d = Decimal(str(raw))
    except ArithmeticError:
        raise SchemaError(f"{where}: must be a number") from None
    if not d.is_finite() or (lo is not None and d < lo) or (hi is not None and d > hi):
        raise SchemaError(f"{where}: out of range")
    return d


def _names(raw, where: str) -> tuple:
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        raise SchemaError(f"{where}: list of field names")
    return tuple(raw)


def _keys(d: dict, allowed: set, where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise SchemaError(f"{where}: unknown keys {sorted(extra)}")


def _need(row: Row, names, codes: list) -> bool:
    """True if every name has a value; else MISSING/INVALID codes are added and False is returned."""
    ok = True
    for n in names:
        if row.values.get(n) is None:
            codes.append(f"{row.errors.get(n, 'MISSING')}:{n}")
            ok = False
    return ok


def _round(x: Decimal, mode: str) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_CEILING if mode == "up" else ROUND_HALF_UP)


def _tally(counter: dict, codes) -> None:
    for c in codes:
        counter[c] = counter.get(c, 0) + 1


# =============================================================================================================
# payroll_register_check
# =============================================================================================================

@dataclass(frozen=True)
class PtSchedule:
    slabs: tuple          # ((from_amount_inclusive, monthly_pt), ...) increasing
    february: dict        # monthly_pt -> February amount


@dataclass(frozen=True)
class PayrollRules:
    tolerance: Decimal
    earnings: tuple
    deductions: tuple
    optional: frozenset
    pf_field: str
    pf_rate: Decimal
    pf_ceiling: Decimal
    pf_wages: tuple
    pf_accept: frozenset
    pf_zero_above_ceiling: bool
    esic_field: str
    esic_rate: Decimal
    esic_ceiling: Decimal
    esic_exempt_below: Decimal | None
    esic_rounding: str
    pt_field: str
    pt_state_field: str
    pt_month_field: str | None
    pt_states: dict       # state -> tuple[PtSchedule] | None (None = not a monthly deduction: not checked)
    pt_state_alias: dict
    tds_field: str
    tds_standard_deduction: Decimal
    tds_no_tax_upto: Decimal
    tds_max_share: Decimal
    tds_months: int
    gross_field: str
    net_field: str


def parse_payroll_rules(body: dict) -> PayrollRules:
    w = "rules"
    _keys(body, {"tolerance", "earnings", "deductions", "optional", "fields", "pf", "esic", "pt", "tds"}, w)
    try:
        f = body.get("fields") or {}
        _keys(f, {"gross", "net", "pf", "esic", "pt", "tds", "state", "month"}, "rules.fields")
        pf, es, pt, tds = body["pf"], body["esic"], body["pt"], body["tds"]
        _keys(pf, {"rate", "wage_ceiling", "wages", "accept", "allow_zero_above_ceiling"}, "rules.pf")
        _keys(es, {"employee_rate", "gross_ceiling", "rounding", "employee_exempt_below_monthly"}, "rules.esic")
        _keys(pt, {"states", "state_alias"}, "rules.pt")
        _keys(tds, {"standard_deduction", "no_tax_upto_annual_taxable", "max_share_of_gross", "annualise_months"},
              "rules.tds")
        states = {}
        for st, spec in (pt["states"] or {}).items():
            if spec is None or spec == "not_monthly":
                states[str(st)] = None
                continue
            scheds = spec if isinstance(spec, list) else [spec]
            out = []
            for sc in scheds:
                _keys(sc, {"slabs", "february"}, f"rules.pt.states.{st}")
                slabs = tuple((_dec(a, f"pt.{st}", 0), _dec(b, f"pt.{st}", 0, 100000)) for a, b in sc["slabs"])
                if not slabs or slabs[0][0] != 0 or any(b[0] <= a[0] for a, b in zip(slabs, slabs[1:], strict=False)):
                    raise SchemaError(f"rules.pt.states.{st}: slabs start at 0 and increase")
                feb = {_dec(k, f"pt.{st}.february"): _dec(v, f"pt.{st}.february", 0, 100000)
                       for k, v in (sc.get("february") or {}).items()}
                out.append(PtSchedule(slabs, feb))
            states[str(st)] = tuple(out)
        accept = frozenset(pf.get("accept") or ["capped"])
        if not accept or accept - {"capped", "uncapped"}:
            raise SchemaError("rules.pf.accept: capped and/or uncapped")
        rounding = str(es.get("rounding", "up"))
        if rounding not in ("up", "nearest"):
            raise SchemaError("rules.esic.rounding: up or nearest")
        exempt = es.get("employee_exempt_below_monthly")
        return PayrollRules(
            tolerance=_dec(body.get("tolerance", 1), "rules.tolerance", 0, 1000),
            earnings=_names(body["earnings"], "rules.earnings"),
            deductions=_names(body["deductions"], "rules.deductions"),
            optional=frozenset(_names(body.get("optional") or [], "rules.optional")),
            pf_field=f.get("pf", "pf_employee"), pf_rate=_dec(pf["rate"], "rules.pf.rate", 0, 1),
            pf_ceiling=_dec(pf["wage_ceiling"], "rules.pf.wage_ceiling", 0), pf_wages=_names(pf["wages"], "pf.wages"),
            pf_accept=accept, pf_zero_above_ceiling=bool(pf.get("allow_zero_above_ceiling", False)),
            esic_field=f.get("esic", "esic_employee"), esic_rate=_dec(es["employee_rate"], "esic.rate", 0, 1),
            esic_ceiling=_dec(es["gross_ceiling"], "esic.gross_ceiling", 0),
            esic_exempt_below=None if exempt is None else _dec(exempt, "esic.exempt", 0), esic_rounding=rounding,
            pt_field=f.get("pt", "professional_tax"), pt_state_field=f.get("state", "state"),
            pt_month_field=f.get("month", "pay_period"), pt_states=states,
            pt_state_alias={str(k).casefold(): str(v) for k, v in (pt.get("state_alias") or {}).items()},
            tds_field=f.get("tds", "tds"),
            tds_standard_deduction=_dec(tds["standard_deduction"], "tds.standard_deduction", 0),
            tds_no_tax_upto=_dec(tds["no_tax_upto_annual_taxable"], "tds.no_tax_upto", 0),
            tds_max_share=_dec(tds["max_share_of_gross"], "tds.max_share_of_gross", 0, 1),
            tds_months=int(tds.get("annualise_months", 12)),
            gross_field=f.get("gross", "gross"), net_field=f.get("net", "net_pay"))
    except SchemaError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise SchemaError(f"payroll rules malformed: {type(e).__name__}") from None


def payroll_inputs(r: PayrollRules) -> dict:
    """Selected names this computation reads, with the dispositions each may have."""
    amounts = {n: ("COMPUTE_ONLY",) for n in (*r.earnings, *r.deductions, r.gross_field, r.net_field, *r.pf_wages,
                                              r.pf_field, r.esic_field, r.pt_field, r.tds_field)}
    out = {**amounts, r.pt_state_field: ("COMPUTE_ONLY", "KEEP")}
    if r.pt_month_field:
        out[r.pt_month_field] = ("COMPUTE_ONLY", "GENERALIZE")
    return out


def _month_of(v) -> int | None:
    if isinstance(v, tuple):
        return v[1]
    if isinstance(v, str) and len(v) == 7 and v[4] == "-":   # generalized "YYYY-MM"
        return int(v[5:])
    return None


def _pt_expected(sched: PtSchedule, wages: Decimal, month: int | None) -> set:
    amt = Decimal(0)
    for frm, a in sched.slabs:
        if wages >= frm:
            amt = a
    if month == 2:
        return {sched.february.get(amt, amt)}
    if month is None:
        return {amt, sched.february.get(amt, amt)}
    return {amt}


def payroll_register_check(rows: list, params: dict, r: PayrollRules, ctx) -> dict:
    counts: dict = {}
    flagged = []
    incomplete = 0
    total_gross = total_net = Decimal(0)
    tol = r.tolerance
    for row in rows:
        codes: list = []
        v = row.values

        vals = {}
        incomplete_row = False
        needed = (*r.earnings, *r.deductions, r.gross_field, r.net_field, *r.pf_wages, r.pf_field, r.esic_field,
                  r.pt_field, r.tds_field)
        for n in dict.fromkeys(needed):
            x = v.get(n)
            if x is None:
                err = row.errors.get(n, "MISSING")
                if n in r.optional and err == "MISSING":
                    vals[n] = Decimal(0)        # an optional pay component the register leaves blank
                    continue
                codes.append(f"{err}:{n}")
                incomplete_row = True
                continue
            vals[n] = _num(x)
            if vals[n] < 0:
                codes.append(f"NEGATIVE:{n}")
        g = vals.get(r.gross_field)
        if g is not None:
            total_gross += g
        if vals.get(r.net_field) is not None:
            total_net += vals[r.net_field]
        if all(n in vals for n in (*r.earnings, r.gross_field)):
            if abs(sum((vals[n] for n in r.earnings), Decimal(0)) - g) > tol:
                codes.append("GROSS_MISMATCH")
        if all(n in vals for n in (*r.deductions, r.gross_field, r.net_field)):
            if abs(g - sum((vals[n] for n in r.deductions), Decimal(0)) - vals[r.net_field]) > tol:
                codes.append("NET_MISMATCH")
        # PF: employee share = rate x PF wages, capped at the wage ceiling (or on full wages if accepted)
        if all(n in vals for n in (*r.pf_wages, r.pf_field)):
            wages = sum((vals[n] for n in r.pf_wages), Decimal(0))
            pf = vals[r.pf_field]
            ok = set()
            if "capped" in r.pf_accept:
                ok.add(_round(r.pf_rate * min(wages, r.pf_ceiling), "nearest"))
            if "uncapped" in r.pf_accept:
                ok.add(_round(r.pf_rate * wages, "nearest"))
            zero_ok = r.pf_zero_above_ceiling and wages > r.pf_ceiling and pf == 0
            if not zero_ok and not any(abs(pf - e) <= tol for e in ok):
                codes.append("PF_MISMATCH")
        # ESIC: employee share only while gross <= the wage ceiling
        if g is not None and r.esic_field in vals:
            es = vals[r.esic_field]
            if g > r.esic_ceiling:
                if es > 0:
                    codes.append("ESIC_ABOVE_CEILING")
            else:
                exempt = r.esic_exempt_below is not None and g <= r.esic_exempt_below
                expected = Decimal(0) if exempt else _round(r.esic_rate * g, r.esic_rounding)
                if expected > 0 and es == 0:
                    codes.append("ESIC_MISSING")
                elif abs(es - expected) > tol:
                    codes.append("ESIC_MISMATCH")
        # PT: state slab (February amount where the state levies one)
        state = v.get(r.pt_state_field)
        if g is not None and r.pt_field in vals:
            if state is None:
                codes.append(f"{row.errors.get(r.pt_state_field, 'MISSING')}:{r.pt_state_field}")
                incomplete_row = True
            else:
                code = r.pt_state_alias.get(str(state).casefold(), str(state))
                if code not in r.pt_states:
                    codes.append("PT_STATE_NOT_CONFIGURED")
                elif r.pt_states[code] is not None:
                    month = _month_of(v.get(r.pt_month_field)) if r.pt_month_field else None
                    ok = set().union(*(_pt_expected(s, g, month) for s in r.pt_states[code]))
                    if not any(abs(vals[r.pt_field] - e) <= tol for e in ok):
                        codes.append("PT_MISMATCH")
        # TDS sanity flags (not a tax computation: regime, declarations and arrears are unknown here)
        if g is not None and r.tds_field in vals:
            t = vals[r.tds_field]
            annual = g * r.tds_months - r.tds_standard_deduction
            if t > 0 and annual <= r.tds_no_tax_upto:
                codes.append("TDS_BELOW_THRESHOLD")
            if t > 0 and (g <= 0 or t / g > r.tds_max_share):
                codes.append("TDS_SHARE_HIGH")
        incomplete += incomplete_row
        if codes:
            codes = sorted(set(codes))
            _tally(counts, codes)
            flagged.append((row, ", ".join(codes)))
    return {"rows_checked": Out("count", len(rows)),
            "rows_with_exceptions": Out("count", len(flagged)),
            "rows_incomplete": Out("count", incomplete),
            "exception_counts": Out("codes", counts),
            "exceptions": Out("list", flagged),
            "total_gross": Out("amount", total_gross),
            "total_net": Out("amount", total_net)}


# =============================================================================================================
# timecard_exceptions
# =============================================================================================================

@dataclass(frozen=True)
class TimecardRules:
    max_shift_hours: Decimal
    max_spread_hours: Decimal | None
    max_weekly_hours: Decimal
    overnight: bool
    f: dict


_TC_FIELDS = {"date": "work_date", "in": "punch_in", "out": "punch_out", "hours": "hours",
              "overtime": "overtime_hours", "approved": "overtime_approved", "absence": "is_absence"}


def parse_timecard_rules(body: dict) -> TimecardRules:
    _keys(body, {"max_shift_hours", "max_spread_hours", "max_weekly_hours", "overnight_shifts", "fields"}, "rules")
    try:
        f = {**_TC_FIELDS, **(body.get("fields") or {})}
        _keys(f, set(_TC_FIELDS), "rules.fields")
        spread = body.get("max_spread_hours")
        return TimecardRules(_dec(body["max_shift_hours"], "max_shift_hours", 1, 24),
                             None if spread is None else _dec(spread, "max_spread_hours", 1, 24),
                             _dec(body["max_weekly_hours"], "max_weekly_hours", 1, 168),
                             bool(body.get("overnight_shifts", True)), f)
    except SchemaError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise SchemaError(f"timecard rules malformed: {type(e).__name__}") from None


def timecard_inputs(r: TimecardRules) -> dict:
    f = r.f
    return {f["date"]: ("COMPUTE_ONLY",), f["in"]: ("COMPUTE_ONLY",), f["out"]: ("COMPUTE_ONLY",),
            f["hours"]: ("COMPUTE_ONLY",), f["overtime"]: ("COMPUTE_ONLY",), f["approved"]: ("COMPUTE_ONLY",),
            f["absence"]: ("PRESENCE",)}


def _dt_of(t, day: tuple) -> _dt.datetime:
    if isinstance(t, _dt.time):              # a time of day: on the work date
        return _dt.datetime(day[0], day[1], day[2], t.hour, t.minute, t.second)
    return _dt.datetime(*t)


def timecard_exceptions(rows: list, params: dict, r: TimecardRules, ctx) -> dict:
    f = r.f
    per: dict = {}           # employee key -> {"row": first row, "codes": {code: n}}
    weekly: dict = {}        # (employee key, iso year, iso week) -> hours
    counts: dict = {}
    absence_days = 0
    absent_people = set()
    for row in rows:
        v = row.values
        key = ctx.identity_key(row)
        ent = per.setdefault(key, {"row": row, "codes": {}})
        codes: list = []
        if v.get(f["absence"]):
            absence_days += 1
            absent_people.add(key)
            continue                     # an absence day: the reason is NEVER and was dropped at parse time
        if not _need(row, (f["date"],), codes):
            _tally(ent["codes"], codes)
            continue
        day = v[f["date"]]
        hours = None
        tin, tout = v.get(f["in"]), v.get(f["out"])
        if (tin is None) != (tout is None) or (tin is None and v.get(f["hours"]) is None):
            codes.append("MISSING_PUNCH")
        if tin is not None and tout is not None:
            a, b = _dt_of(tin, day), _dt_of(tout, day)
            if b <= a and r.overnight:
                b += _dt.timedelta(days=1)
            if b <= a:
                codes.append("PUNCH_ORDER")
            else:
                spread = Decimal((b - a).total_seconds()) / 3600
                hours = spread
                if r.max_spread_hours is not None and spread > r.max_spread_hours:
                    codes.append("SPREAD_OVER_LIMIT")
        if v.get(f["hours"]) is not None:
            hours = _num(v[f["hours"]])          # the recorded worked hours (net of breaks) take precedence
        if hours is not None:
            if hours < 0:
                codes.append(f"NEGATIVE:{f['hours']}")
            elif hours > r.max_shift_hours:
                codes.append("SHIFT_OVER_LIMIT")
            iso = _dt.date(day[0], day[1], day[2]).isocalendar()
            wk = (key, iso[0], iso[1])
            weekly[wk] = weekly.get(wk, Decimal(0)) + max(hours, Decimal(0))
        ot = v.get(f["overtime"])
        worked_over = hours is not None and hours > r.max_shift_hours
        if (ot is not None and _num(ot) > 0) or worked_over:
            appr = v.get(f["approved"])
            if appr is None:
                if f["approved"] in row.errors and row.errors[f["approved"]] == "INVALID":
                    codes.append(f"INVALID:{f['approved']}")
                else:
                    codes.append("OT_WITHOUT_APPROVAL")
            elif not appr:
                codes.append("OT_WITHOUT_APPROVAL")
        _tally(ent["codes"], codes)
    for (key, _y, _w), h in weekly.items():
        if h > r.max_weekly_hours:
            _tally(per[key]["codes"], ["WEEKLY_OVER_LIMIT"])
    flagged = []
    for ent in per.values():
        if ent["codes"]:
            for c, n in ent["codes"].items():
                counts[c] = counts.get(c, 0) + n
            flagged.append((ent["row"], ", ".join(f"{c} x{n}" for c, n in sorted(ent["codes"].items()))))
    return {"rows_checked": Out("count", len(rows)), "employees_checked": Out("count", len(per)),
            "employees_with_exceptions": Out("count", len(flagged)), "exception_counts": Out("codes", counts),
            "exceptions": Out("list", flagged), "absence_days": Out("count", absence_days),
            "employees_with_absence": Out("count", len(absent_people))}


# =============================================================================================================
# contractor_budget
# =============================================================================================================

@dataclass(frozen=True)
class BudgetRules:
    near_pct: Decimal
    rate_tolerance_pct: Decimal
    util_edges: tuple
    util_labels: tuple
    excess_edges: tuple
    excess_labels: tuple
    f: dict


_CB_FIELDS = {"budget": "po_budget", "spend": "spend_to_date", "rate": "bill_rate", "approved": "approved_rate",
              "group": "cost_center"}


def parse_budget_rules(body: dict) -> BudgetRules:
    _keys(body, {"near_budget_pct", "rate_tolerance_pct", "utilisation_bands_pct", "rate_excess_bands_pct",
                 "fields"}, "rules")
    try:
        f = {**_CB_FIELDS, **(body.get("fields") or {})}
        _keys(f, set(_CB_FIELDS), "rules.fields")
        ue = parse_edges(body["utilisation_bands_pct"], "utilisation_bands_pct", min_width=False)
        xe = parse_edges(body["rate_excess_bands_pct"], "rate_excess_bands_pct", min_width=False)
        return BudgetRules(_dec(body["near_budget_pct"], "near_budget_pct", 0, 100),
                           _dec(body.get("rate_tolerance_pct", 0), "rate_tolerance_pct", 0, 100),
                           ue, tuple(band_labels(list(ue), "%")), xe, tuple(band_labels(list(xe), "%")), f)
    except SchemaError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise SchemaError(f"contractor budget rules malformed: {type(e).__name__}") from None


def budget_inputs(r: BudgetRules) -> dict:
    f = r.f
    return {f["budget"]: ("COMPUTE_ONLY",), f["spend"]: ("COMPUTE_ONLY",), f["rate"]: ("COMPUTE_ONLY",),
            f["approved"]: ("COMPUTE_ONLY",), f["group"]: ("KEEP", "OPTIONAL")}


def band_of(x: Decimal, edges: tuple, labels: tuple) -> str:
    idx = 0
    for i, e in enumerate(edges):
        if x >= e:
            idx = i
    return labels[idx] if x >= edges[0] else labels[0]


def _person(ctx, row):
    fn = getattr(ctx, "person_key", None)
    return fn(row) if fn is not None else ("#row", row.index)


def contractor_budget(rows: list, params: dict, r: BudgetRules, ctx) -> dict:
    f = r.f
    counts: dict = {}
    flagged = []
    groups: dict = {}
    tot_spend = tot_budget = Decimal(0)
    for row in rows:
        v = row.values
        codes: list = []
        tokens: list = []
        if _need(row, (f["budget"], f["spend"]), codes):
            b, s = _num(v[f["budget"]]), _num(v[f["spend"]])
            if b < 0 or s < 0:
                codes.append("NEGATIVE_AMOUNT")
            elif b == 0:
                codes.append("NO_BUDGET")
            else:
                util = s * 100 / b
                tokens.append("utilisation " + band_of(util, r.util_edges, r.util_labels))
                if s > b:
                    codes.append("OVER_BUDGET")
                elif util >= r.near_pct:
                    codes.append("NEAR_BUDGET")
                tot_spend += s
                tot_budget += b
                gv = v.get(f["group"])
                if gv is not None:
                    g = groups.setdefault(gv, [Decimal(0), Decimal(0), set()])
                    g[0] += s
                    g[1] += b
                    g[2].add(_person(ctx, row))
        if _need(row, (f["rate"], f["approved"]), codes):
            rate, appr = _num(v[f["rate"]]), _num(v[f["approved"]])
            if rate < 0 or appr <= 0:
                codes.append("INVALID_RATE")
            elif rate > appr * (1 + r.rate_tolerance_pct / 100):
                codes.append("RATE_ABOVE_APPROVED")
                tokens.append("rate above approved by " + band_of((rate - appr) * 100 / appr, r.excess_edges,
                                                                   r.excess_labels))
        if codes:
            codes = sorted(set(codes))
            _tally(counts, codes)
            flagged.append((row, "; ".join([", ".join(codes), *tokens])))
    # Small-group control (pgw.product.disclosure): a cost centre with fewer than k people is merged into "other
    # cost centres", which is itself shown only with at least k people; otherwise its band is suppressed.
    k = getattr(ctx, "k", 1)
    group_rows, small = [], []
    for key, (s, b, people) in sorted(groups.items()):
        if b <= 0:
            continue
        if len(people) >= k:
            group_rows.append((key, "utilisation " + band_of(s * 100 / b, r.util_edges, r.util_labels)))
        else:
            small.append((s, b, people))
    if small:
        ms = sum((x[0] for x in small), Decimal(0))
        mb = sum((x[1] for x in small), Decimal(0))
        mp = set().union(*(x[2] for x in small))
        ok = len(mp) >= k and mb > 0
        group_rows.append(("other cost centres", "utilisation " + band_of(ms * 100 / mb, r.util_edges, r.util_labels)
                           if ok else "suppressed"))
        st = getattr(ctx, "stats", None)
        if st is not None:
            st["groups_merged"] = st.get("groups_merged", 0) + len(small)
            st["groups_suppressed"] = st.get("groups_suppressed", 0) + (0 if ok else 1)
    return {"rows_checked": Out("count", len(rows)), "rows_flagged": Out("count", len(flagged)),
            "flag_counts": Out("codes", counts), "flagged": Out("list", flagged),
            "group_utilisation": Out("groups", group_rows), "total_spend": Out("amount", tot_spend),
            "total_budget": Out("amount", tot_budget),
            "overall_utilisation_pct": Out("percent", tot_spend * 100 / tot_budget if tot_budget else Decimal(0))}


# =============================================================================================================
# roster_question (generic)
# =============================================================================================================

def parse_roster_rules(body: dict) -> dict:
    _keys(body, {"max_filter_clauses", "max_in_values"}, "rules")
    return {"max_filter_clauses": int(body.get("max_filter_clauses", 10)),
            "max_in_values": int(body.get("max_in_values", 50))}


OPS = ("eq", "ne", "in", "gte", "lte")


def roster_match(row: Row, flt) -> int:
    """1: the row matches every clause; -1: a filtered field has no value (counted as missing); 0: no match.
    Every row is exactly one of the three (the roster's count table relies on that partition)."""
    for name, op, val, order in flt:
        x = row.values.get(name)
        if x is None:
            return -1
        if op == "eq":
            ok = x == val
        elif op == "ne":
            ok = x != val
        elif op == "in":
            ok = x in val
        else:
            kx, kv = order(x), order(val)
            ok = kx is not None and (kx >= kv if op == "gte" else kx <= kv)
        if not ok:
            return 0
    return 1


def roster_question(rows: list, params: dict, rules: dict, ctx) -> dict:
    """params (validated by pgw.product.records against the contract): filter [(name, op, value)], group_by,
    list. Only KEEP and GENERALIZE fields can be filtered or grouped on, and only with sealed values.
    Listed rows carry no attributes here: the renderer adds the contract's `show` fields after disclosure control."""
    flt = params["filter"]
    matched = []
    missing = 0
    for row in rows:
        m = roster_match(row, flt)
        if m == 1:
            matched.append(row)
        elif m == -1:
            missing += 1
    gb = params.get("group_by")
    groups, sizes = [], []
    if gb:
        gc: dict = {}
        for row in matched:
            k = row.values.get(gb)
            gc[k] = gc.get(k, 0) + 1
        ordered = sorted(gc.items(), key=lambda kv: (kv[0] is None, str(kv[0])))
        groups = [(k if k is not None else "(not recorded)", f"{n}") for k, n in ordered]
        sizes = ordered
    listed = [(row, "") for row in matched] if params.get("list") else []
    filtered = bool(flt)
    return {"total_rows": Out("count", len(rows)),
            "matched_count": Out("count", len(matched),
                                 {"filtered": filtered, "rest": len(rows) - len(matched) - missing,
                                  "matched": frozenset(r.index for r in matched)}),
            "rows_missing_filter_field": Out("count", missing), "filter_text": Out("label", params["filter_text"]),
            "matched": Out("list", listed, {"population": matched}),
            "group_counts": Out("groups", groups, {"sizes": sizes, "field": gb})}


@dataclass(frozen=True)
class Computation:
    fn: object
    parse_rules: object
    inputs: object            # rules -> {name: allowed dispositions}
    catalog: dict             # output name -> kind
    needs_rules: bool = True


COMPUTATIONS = {
    "payroll_register_check": Computation(
        payroll_register_check, parse_payroll_rules, payroll_inputs,
        {"rows_checked": "count", "rows_with_exceptions": "count", "rows_incomplete": "count",
         "exception_counts": "codes", "exceptions": "list", "total_gross": "amount", "total_net": "amount"}),
    "timecard_exceptions": Computation(
        timecard_exceptions, parse_timecard_rules, timecard_inputs,
        {"rows_checked": "count", "employees_checked": "count", "employees_with_exceptions": "count",
         "exception_counts": "codes", "exceptions": "list", "absence_days": "count",
         "employees_with_absence": "count"}),
    "contractor_budget": Computation(
        contractor_budget, parse_budget_rules, budget_inputs,
        {"rows_checked": "count", "rows_flagged": "count", "flag_counts": "codes", "flagged": "list",
         "group_utilisation": "groups", "total_spend": "amount", "total_budget": "amount",
         "overall_utilisation_pct": "percent"}),
    "roster_question": Computation(
        roster_question, parse_roster_rules, lambda r: {},
        {"total_rows": "count", "matched_count": "count", "rows_missing_filter_field": "count",
         "filter_text": "label", "matched": "list", "group_counts": "groups"}, needs_rules=False),
}
