"""NPT (non-productive time) analytics.

NPT can be shown as hours, or as a percentage of the hours *available*. What
"available" means depends on the client's basis (see ClientNPTSetting):

  monthly 24 h   24 x days x the client's units
  monthly 12 h   12 x days x the client's units
  call-out       (12 or 24) x the days of each call-out job

Everything here is plain Python over lists, so it can be tested without the web layer.
"""
import calendar
import datetime
import math

from .models import NPT_CATEGORY_CHOICES

CATEGORY_LABELS = dict(NPT_CATEGORY_CHOICES)
PERIOD_CHOICES = [
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("last_3_months", "Last 3 months"),
    ("this_year", "This year"),
    ("month", "A specific month"),
    ("custom", "From – to dates"),
]
DEFAULT_PERIOD = "last_3_months"
NEAR_TARGET = 0.8      # 80% of a KPI's target is "getting close"
# Categorical colours for clients and units: the brand colours first, then tints and shades of them.
PALETTE = ["#43358A", "#00A3E4", "#F5E100", "#7C6BC4", "#0077A8", "#6B6880", "#A89DE0", "#66CBF0", "#BDB300", "#2E2566"]


# ── dates ────────────────────────────────────────────────────────────────
def month_start(d):
    return d.replace(day=1)


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d, n):
    index = d.month - 1 + n
    return datetime.date(d.year + index // 12, index % 12 + 1, 1)


def months_between(start, end):
    """First day of every month from start's month to end's month."""
    months, current = [], month_start(start)
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def _date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def parse_period(params, today=None):
    """Turn the ?period=… (&month=YYYY-MM | &from=&to=) query into a date range."""
    today = today or datetime.date.today()
    key = params.get("period") or DEFAULT_PERIOD
    start = end = None

    if key == "this_month":
        start, end = month_start(today), month_end(today)
    elif key == "last_month":
        first = add_months(month_start(today), -1)
        start, end = first, month_end(first)
    elif key == "last_3_months":
        start, end = add_months(month_start(today), -2), month_end(today)
    elif key == "this_year":
        start, end = datetime.date(today.year, 1, 1), datetime.date(today.year, 12, 31)
    elif key == "month":
        picked = _date((params.get("month") or "") + "-01")
        if picked:
            start, end = picked, month_end(picked)
    elif key == "custom":
        first, last = _date(params.get("from")), _date(params.get("to"))
        if first and last:
            start, end = min(first, last), max(first, last)

    if start is None:                      # anything unusable falls back to the default
        key = DEFAULT_PERIOD
        start, end = add_months(month_start(today), -2), month_end(today)

    if key == "month":
        label = start.strftime("%B %Y")
    elif key == "this_year":
        label = str(start.year)
    else:
        label = f"{start:%d %b %Y} – {end:%d %b %Y}"
    return {"key": key, "start": start, "end": end, "label": label,
            "month_value": start.strftime("%Y-%m"), "from_value": start.isoformat(), "to_value": end.isoformat()}


def clip_to_today(start, end, today=None):
    """The part of [start, end] that has happened. None if it is all in the future."""
    today = today or datetime.date.today()
    if start > today:
        return None
    return start, min(end, today)


def overlap_days(a_start, a_end, b_start, b_end):
    first, last = max(a_start, b_start), min(a_end, b_end)
    return max(0, (last - first).days + 1)


def focus_month(start, end, today=None):
    """The month a KPI is judged on: the latest month of the range that has begun."""
    today = today or datetime.date.today()
    return month_start(min(end, today) if start <= today else start)


# ── entries ──────────────────────────────────────────────────────────────
def hours(entry):
    return float(entry.npt_hours or 0)


def in_range(entries, start, end):
    return [e for e in entries if e.date and start <= e.date <= end]


def apply_filters(entries, client="", unit_id="", category=""):
    out = entries
    if client:
        out = [e for e in out if e.client == client]
    if unit_id:
        out = [e for e in out if str(e.unit_id) == str(unit_id)]
    if category:
        out = [e for e in out if e.category == category]
    return out


# ── available hours (what NPT is measured against) ───────────────────────
def available_hours(setting, start, end, jobs=(), entries=(), unit_ids=None, today=None):
    """Hours available for one client in [start, end], up to today.

    `unit_ids` narrows it to some of the client's units. For call-outs that
    means only the jobs those units logged NPT on."""
    window = clip_to_today(start, end, today)
    if window is None:
        return 0.0
    first, last = window
    if setting.basis == setting.CALLOUT:
        relevant = [j for j in jobs if j.callout_start and j.callout_end and j.manifest.client == setting.client]
        if unit_ids is not None:
            with_npt = {e.job_report_id for e in entries if e.unit_id in unit_ids}
            relevant = [j for j in relevant if j.pk in with_npt]
        return float(sum(overlap_days(j.callout_start, j.callout_end, first, last) * setting.hours_per_day
                         for j in relevant))
    units = {u.pk for u in setting.units.all()}
    if unit_ids is not None:
        units &= set(unit_ids)
    return float(setting.hours_per_day * ((last - first).days + 1) * len(units))


def available_for(settings, start, end, jobs=(), entries=(), client="", unit_ids=None, today=None):
    """Available hours for one client (when `client` is given) or every client that has a setup.
    Returns (hours, has_setup)."""
    chosen = [s for s in settings if not client or s.client == client]
    if not chosen:
        return 0.0, False
    return sum(available_hours(s, start, end, jobs, entries, unit_ids, today) for s in chosen), True


def percent_of(npt_hours, available):
    return round(100 * npt_hours / available, 2) if available else None


# ── chart ────────────────────────────────────────────────────────────────
def nice_axis(peak, line=None, want_ticks=4):
    """A round top value and tick marks, so the y-axis reads 0, 5, 10, 15, 20."""
    target = max(peak, (line or 0) * 1.15, 0.0001)
    raw = target / want_ticks
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    top = step * math.ceil(target / step)
    count = round(top / step)
    return top, [round(step * i, 4) for i in range(count + 1)]


def time_series(entries, start, end, kpi_line=None, metric="hours", settings=(), jobs=(), client="",
                unit_ids=None, today=None, all_entries=None):
    """Bars for the 'NPT over time' chart: per day for short ranges, per month otherwise.

    metric "percent" divides each bar's NPT by the hours available in that bar's window.
    `all_entries` (not narrowed by cause) is used for the availability of call-outs."""
    today = today or datetime.date.today()
    # An hours target is a monthly figure, so with an hours KPI the bars are whole months.
    daily = (end - start).days <= 31 and not (metric == "hours" and kpi_line)
    if daily:
        buckets = [(start + datetime.timedelta(days=i),) * 2 for i in range((end - start).days + 1)]
    else:
        buckets = [(max(m, start), min(month_end(m), end)) for m in months_between(start, end)]
    values, avail = {}, {}
    for first, last in buckets:
        total = sum(hours(e) for e in entries if first <= e.date <= last)
        if metric == "percent":
            available, _ = available_for(settings, first, last, jobs, all_entries if all_entries is not None else entries,
                                         client, unit_ids, today)
            avail[first] = available
            values[first] = percent_of(total, available) or 0.0
        else:
            values[first] = total
    use_line = kpi_line
    peak = max(list(values.values()) + [0])
    top, ticks = nice_axis(peak, use_line)
    first_key = min(values)
    bars = [{
        "label": d.strftime("%d") if daily else d.strftime("%b"),
        "sub": "" if daily else (d.strftime("%Y") if d == first_key or d.month == 1 else ""),
        "title": d.strftime("%d %b %Y") if daily else d.strftime("%B %Y"),
        "value": round(v, 2 if metric == "percent" else 1),
        "hours": round(sum(hours(e) for e in entries if (d <= e.date <= (d if daily else month_end(d)))), 1),
        "available": round(avail.get(d, 0.0), 1),
        "height": round(100 * v / top, 1) if v else 0,
        "over": bool(use_line and v > use_line),
    } for d, v in values.items()]
    return {
        "bars": bars, "grouping": "day" if daily else "month", "metric": metric,
        "top": top, "ticks": [{"value": t, "bottom": round(100 * t / top, 2)} for t in ticks],
        "kpi_line": use_line, "kpi_bottom": round(100 * use_line / top, 2) if use_line else None,
    }


def breakdown(entries, key, label, colour=None):
    """Hours, entry count and share per key (cause / client…), biggest first."""
    groups = {}
    for e in entries:
        k = key(e)
        row = groups.setdefault(k, {"key": k, "label": label(e), "hours": 0.0, "count": 0})
        row["hours"] += hours(e)
        row["count"] += 1
    rows = sorted(groups.values(), key=lambda r: (-r["hours"], r["label"]))
    total = sum(r["hours"] for r in rows) or 1
    biggest = rows[0]["hours"] if rows else 1
    for r in rows:
        r["hours"] = round(r["hours"], 1)
        r["share"] = round(100 * r["hours"] / total)
        r["width"] = max(3, round(100 * r["hours"] / biggest)) if r["hours"] else 0
        if colour:
            r["colour"] = colour(r["key"])
    return rows


def colour_map(names):
    """A stable colour for each name: the same client gets the same colour on every page."""
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(sorted({n for n in names if n}))}


# ── KPIs ─────────────────────────────────────────────────────────────────
def kpi_matches(kpi, entry):
    return ((not kpi.unit_id or entry.unit_id == kpi.unit_id)
            and (not kpi.client or entry.client == kpi.client)
            and (not kpi.category or entry.category == kpi.category))


def _status(actual, target, known=True):
    if not known:
        return "unknown"
    ratio = actual / target if target else 0
    return "over" if actual > target else "near" if ratio >= NEAR_TARGET else "ok"


def evaluate_kpis(kpis, entries, start, end, today=None, settings=(), jobs=()):
    """Compare each KPI with the actual NPT in its scope, judged on the latest month.

    `entries` should be every entry in the date range (ignoring the page's own
    client / unit / cause filters, since a KPI has its own scope). A percentage
    KPI needs the client's NPT setup to know the hours available; without one
    its status is "unknown"."""
    today = today or datetime.date.today()
    focus = focus_month(start, end, today)
    months = [m for m in months_between(start, end) if m <= today or m == focus]
    rows = []
    for kpi in kpis:
        target = float(kpi.target)
        percent = kpi.metric == "percent"
        scope = [e for e in entries if kpi_matches(kpi, e)]
        unit_ids = [kpi.unit_id] if kpi.unit_id else None

        def month_value(m):
            first, last = max(m, start), min(month_end(m), end)
            npt_hours = sum(hours(e) for e in scope if first <= e.date <= last)
            if not percent:
                return npt_hours, None, True
            available, known = available_for(settings, first, last, jobs, entries, kpi.client, unit_ids, today)
            return npt_hours, available, bool(known and available)

        per_month = {m: month_value(m) for m in months}
        npt_hours, available, known = per_month.get(focus, (0.0, None, not percent))
        actual = round(percent_of(npt_hours, available) or 0.0, 2) if percent else round(npt_hours, 1)
        over = lambda v: (percent_of(v[0], v[1]) or 0) > target if percent else v[0] > target
        ratio = actual / target if target else 0
        rows.append({
            "kpi": kpi, "target": target, "actual": actual, "focus": focus, "metric": kpi.metric,
            "available": round(available, 1) if available is not None else None, "npt_hours": round(npt_hours, 1),
            "percent": round(100 * ratio), "bar": min(100, round(100 * ratio)),
            "status": _status(actual, target, known),
            "months_over": sum(1 for v in per_month.values() if over(v)),
            "months_total": len(months),
            "period_hours": round(sum(v[0] for v in per_month.values()), 1),
        })
    return rows


def pick_chart_kpi(kpis, metric, client="", unit_id="", category=""):
    """The KPI to draw as the line on the chart: the one that matches what is being
    looked at most closely (same client / unit / cause), falling back to broader ones."""
    candidates = [k for k in kpis if k.metric == metric]
    uid = int(unit_id) if str(unit_id).isdigit() else None
    for want in ((client, uid, category), (client, uid, ""), (client, None, ""), ("", None, "")):
        for k in candidates:
            if (k.client, k.unit_id, k.category) == want:
                return k
    if uid and not client:      # a unit picked without a client: use that unit's own KPI
        for k in candidates:
            if k.unit_id == uid and not k.category:
                return k
    return None


# ── units and clients ────────────────────────────────────────────────────
def unit_table(units, entries, settings=(), jobs=(), start=None, end=None, today=None):
    """One row per unit, *including units with no NPT*, biggest first.

    With settings (and a period) each row also gets the unit's NPT % of the hours
    available to it. Units that have entries but are no longer marked as units (and
    entries with no unit at all) still get a row, so totals always add up."""
    by_id = {u.pk: u for u in units}
    for e in entries:
        if e.unit_id and e.unit_id not in by_id:
            by_id[e.unit_id] = e.unit
    rows = {pk: {"key": pk, "label": u.name, "sub": u.description, "hours": 0.0, "count": 0,
                 "department": u.classification.department.name, "percent": None} for pk, u in by_id.items()}
    unassigned = {"key": None, "label": "No unit recorded", "sub": "", "hours": 0.0, "count": 0, "department": "", "percent": None}
    for e in entries:
        row = rows[e.unit_id] if e.unit_id else unassigned
        row["hours"] += hours(e)
        row["count"] += 1
    if settings and start and end:
        for pk, row in rows.items():
            mine = [s for s in settings if pk in {u.pk for u in s.units.all()} or s.basis == s.CALLOUT]
            available = 0.0
            counted = 0.0
            for s in mine:
                mine_entries = [e for e in entries if e.unit_id == pk and e.client == s.client]
                a = available_hours(s, start, end, jobs, mine_entries, [pk], today)
                if a:
                    available += a
                    counted += sum(hours(e) for e in mine_entries)
            row["percent"] = percent_of(counted, available)
            row["available"] = round(available, 1)
    result = list(rows.values()) + ([unassigned] if unassigned["count"] else [])
    result.sort(key=lambda r: (-r["hours"], r["label"]))
    biggest = max((r["hours"] for r in result), default=0) or 1
    for r in result:
        r["hours"] = round(r["hours"], 1)
        r["width"] = max(3, round(100 * r["hours"] / biggest)) if r["hours"] else 0
    return result


def client_table(client_names, entries, settings, jobs, start, end, today=None, colours=None):
    """One row per client: NPT hours, hours available on its basis, NPT %, and its basis."""
    by_setting = {s.client: s for s in settings}
    rows = []
    for name in sorted({n for n in client_names if n}):
        mine = [e for e in entries if e.client == name]
        npt_hours = sum(hours(e) for e in mine)
        setting = by_setting.get(name)
        available = available_hours(setting, start, end, jobs, entries, None, today) if setting else None
        rows.append({
            "name": name, "colour": (colours or {}).get(name, PALETTE[0]), "hours": round(npt_hours, 1),
            "count": len(mine), "setting": setting, "basis": setting.basis_text if setting else "",
            "available": round(available, 1) if available is not None else None,
            "percent": percent_of(npt_hours, available) if available else None,
        })
    rows.sort(key=lambda r: (-r["hours"], r["name"]))
    biggest = max((r["hours"] for r in rows), default=0) or 1
    for r in rows:
        r["width"] = max(3, round(100 * r["hours"] / biggest)) if r["hours"] else 0
    return rows


def compare_units(units, entries, setting, jobs, start, end, metric="hours", today=None, monthly=False):
    """Side-by-side figures and a grouped chart for two or more units of one client."""
    today = today or datetime.date.today()
    daily = (end - start).days <= 31 and not monthly
    if daily:
        buckets = [(start + datetime.timedelta(days=i),) * 2 for i in range((end - start).days + 1)]
    else:
        buckets = [(max(m, start), min(month_end(m), end)) for m in months_between(start, end)]
    per_unit = {}
    for i, unit in enumerate(units):
        mine = [e for e in entries if e.unit_id == unit.pk]
        causes = {}
        for e in mine:
            causes[e.category] = causes.get(e.category, 0.0) + hours(e)
        series = []
        for first, last in buckets:
            npt_hours = sum(hours(e) for e in mine if first <= e.date <= last)
            value = npt_hours
            if metric == "percent" and setting:
                value = percent_of(npt_hours, available_hours(setting, first, last, jobs, mine, [unit.pk], today)) or 0.0
            series.append(value)
        total_available = available_hours(setting, start, end, jobs, mine, [unit.pk], today) if setting else 0.0
        total = sum(hours(e) for e in mine)
        top_cause = max(causes.items(), key=lambda kv: kv[1])[0] if causes else ""
        per_unit[unit.pk] = {
            "unit": unit, "colour": PALETTE[i % len(PALETTE)], "hours": round(total, 1), "count": len(mine),
            "available": round(total_available, 1), "percent": percent_of(total, total_available),
            "top_cause": CATEGORY_LABELS.get(top_cause, ""), "series": series,
        }
    return {"units": list(per_unit.values()), "buckets": buckets, "daily": daily}


def compare_chart(comparison, kpi_line=None, metric="hours"):
    """Grouped bars for compare_units(): one coloured bar per unit, in every time bucket."""
    values = [v for u in comparison["units"] for v in u["series"]]
    use_line = kpi_line
    top, ticks = nice_axis(max(values + [0]), use_line)
    groups = []
    for i, (first, _last) in enumerate(comparison["buckets"]):
        groups.append({
            "label": first.strftime("%d") if comparison["daily"] else first.strftime("%b"),
            "sub": first.strftime("%Y") if (not comparison["daily"] and (i == 0 or first.month == 1)) else "",
            "bars": [{
                "unit": u["unit"].name, "colour": u["colour"], "value": round(u["series"][i], 2),
                "height": round(100 * u["series"][i] / top, 1) if u["series"][i] else 0,
                "over": bool(use_line and u["series"][i] > use_line),
            } for u in comparison["units"]],
        })
    return {
        "groups": groups, "top": top, "metric": metric,
        "ticks": [{"value": t, "bottom": round(100 * t / top, 2)} for t in ticks],
        "kpi_line": use_line, "kpi_bottom": round(100 * use_line / top, 2) if use_line else None,
    }
