"""Plain-Python analytics for the Action Tracker insights page: who raised how many actions,
of what type, per month, and how long they took to close."""
import datetime
import statistics

from sqb import npt

from .models import INTERNAL, TYPE_LABELS


def raised_on(action):
    """The date an action counts for: when it was raised, else None (old actions)."""
    return action.raised_date


def in_period(actions, start, end):
    return [a for a in actions if a.raised_date and start <= a.raised_date <= end]


def apply_filters(actions, client="", action_type=""):
    return [a for a in actions
            if (not client or (a.client or "") == client) and (not action_type or a.action_type == action_type)]


def _days(actions):
    return [a.days_to_close for a in actions if a.days_to_close is not None]


def timing(actions):
    """Average / median / slowest days to close over the closed actions with known dates."""
    d = _days(actions)
    if not d:
        return {"count": 0, "average": None, "median": None, "slowest": None}
    return {"count": len(d), "average": round(sum(d) / len(d), 1), "median": round(statistics.median(d), 1), "slowest": max(d)}


def summary(actions):
    closed = [a for a in actions if not a.is_open]
    return {"raised": len(actions), "open": len(actions) - len(closed), "closed": len(closed),
            "overdue": sum(a.is_overdue for a in actions), **{"timing": timing(actions)}}


def group(actions, key, label, colour=None):
    """One row per key (client / type): counts, share, average days to close. Biggest first."""
    rows = {}
    for a in actions:
        k = key(a)
        rows.setdefault(k, {"key": k, "label": label(a), "actions": []})["actions"].append(a)
    total = len(actions) or 1
    out = []
    for r in rows.values():
        acts = r.pop("actions")
        s = summary(acts)
        s.update(r)
        s["share"] = round(100 * len(acts) / total)
        s["average"] = s["timing"]["average"]
        if colour:
            s["colour"] = colour(r["key"])
        out.append(s)
    biggest = max([r["raised"] for r in out] + [1])
    for r in out:
        r["width"] = max(3, round(100 * r["raised"] / biggest))
    return sorted(out, key=lambda r: (-r["raised"], r["label"]))


def per_month(actions, start, end, colours=None, split_by_client=False):
    """Bars: actions raised in each month. With split_by_client each bar is stacked by client."""
    months = npt.months_between(start, end)
    counts = {m: {} for m in months}
    for a in actions:
        m = npt.month_start(a.raised_date)
        if m in counts:
            who = (a.client or "Not recorded") if split_by_client else "all"
            counts[m][who] = counts[m].get(who, 0) + 1
    peak = max([sum(c.values()) for c in counts.values()] + [0])
    top, ticks = npt.nice_axis(peak)
    bars = []
    for i, (m, c) in enumerate(counts.items()):
        total = sum(c.values())
        segments = [{"label": who, "count": n, "height": round(100 * n / top, 1),
                     "colour": (colours or {}).get(who, "#6B6880") if split_by_client else "#43358A"}
                    for who, n in sorted(c.items())]
        bars.append({"label": m.strftime("%b"), "sub": m.strftime("%Y") if i == 0 or m.month == 1 else "",
                     "title": m.strftime("%B %Y"), "total": total, "segments": segments,
                     "height": round(100 * total / top, 1) if total else 0})
    return {"bars": bars, "top": top, "ticks": [{"value": t, "bottom": round(100 * t / top, 2)} for t in ticks]}


def slowest_open(actions, limit=5):
    rows = [a for a in actions if a.age_days is not None]
    return sorted(rows, key=lambda a: -a.age_days)[:limit]


def client_label(a):
    return a.client or "Not recorded"


def type_label(a):
    return TYPE_LABELS.get(a.action_type, "Not classified")
