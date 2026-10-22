"""Numbers and lists for the dashboard, gathered in a handful of queries."""
import datetime

from django.db.models import Count, Q
from django.urls import reverse

SOON_DAYS = 30


def _health(qs, date_field):
    """Split records into valid / expiring soon / expired / no date."""
    today = datetime.date.today()
    soon = today + datetime.timedelta(days=SOON_DAYS)
    counts = qs.aggregate(
        expired=Count("pk", filter=Q(**{f"{date_field}__lt": today})),
        soon=Count("pk", filter=Q(**{f"{date_field}__gte": today, f"{date_field}__lte": soon})),
        valid=Count("pk", filter=Q(**{f"{date_field}__gt": soon})),
        missing=Count("pk", filter=Q(**{f"{date_field}__isnull": True})),
    )
    total = sum(counts.values())
    counts["total"] = total
    counts["percent_ok"] = round(100 * counts["valid"] / total) if total else 0
    return counts


def donut_gradient(health):
    """CSS conic-gradient for a donut: valid, soon, expired, missing."""
    total = health["total"]
    if not total:
        return "conic-gradient(var(--border) 0 100%)"
    parts, start = [], 0.0
    for key, colour in (("valid", "var(--success)"), ("soon", "var(--warning)"),
                        ("expired", "var(--danger)"), ("missing", "var(--text-muted)")):
        share = 100 * health[key] / total
        if share:
            parts.append(f"{colour} {start:.2f}% {start + share:.2f}%")
            start += share
    return "conic-gradient(" + ", ".join(parts) + ")"


def _bars(rows, colours=None):
    """rows: [(label, count)] -> [{label, count, width, colour}] scaled to the biggest."""
    biggest = max((c for _, c in rows), default=0) or 1
    return [
        {"label": label, "count": count, "width": max(4, round(100 * count / biggest)) if count else 0,
         "colour": (colours or {}).get(label, "var(--accent)")}
        for label, count in rows
    ]


def _attention_items(today):
    """The soonest expiries / due dates (or already overdue) across modules."""
    from departments.models import AssetCert
    from maintenance.models import MaintenanceRecord
    from personnel.models import PersonnelCert
    from work_approval.models import WARecord

    cutoff = today + datetime.timedelta(days=SOON_DAYS)
    items = []

    def add(kind, label, date, url):
        days = (date - today).days
        items.append({"kind": kind, "label": label, "date": date, "days": days, "url": url,
                      "overdue": days < 0, "days_abs": abs(days)})

    for c in (AssetCert.objects.filter(not_applicable=False, expiry_date__lte=cutoff)
              .select_related("asset", "cert_type").order_by("expiry_date")[:12]):
        add("Equipment cert", f"{c.asset.name} · {c.cert_type.name}", c.expiry_date,
            reverse("departments:classification_detail", args=[c.asset.classification_id]))
    for c in (PersonnelCert.objects.filter(expiry_date__lte=cutoff, personnel__status="Active")
              .select_related("personnel", "course").order_by("expiry_date")[:12]):
        add("Personnel cert", f"{c.personnel.name} · {c.course.course_name}", c.expiry_date, reverse("personnel:list"))
    for r in (WARecord.objects.filter(expiry_date__lte=cutoff)
              .select_related("file_type").order_by("expiry_date")[:8]):
        add("Work approval", f"{r.file_type} {('· ' + r.details) if r.details else ''}".strip(), r.expiry_date,
            reverse("work_approval:list"))
    for m in (MaintenanceRecord.objects.filter(is_active=True, due_date__lte=cutoff)
              .select_related("maintenance_level").order_by("due_date")[:8]):
        what = "Pressure test" if m.activity == MaintenanceRecord.PRESSURE_TEST else (
            m.maintenance_level.level_name if m.maintenance_level else "Maintenance")
        add("Pressure test" if m.activity == MaintenanceRecord.PRESSURE_TEST else "Maintenance",
            f"{m.asset_serial} · {what}", m.due_date, reverse("maintenance:list"))
    items.sort(key=lambda i: i["date"])
    return items[:10], len(items)


def build():
    from action_tracker.models import Action
    from departments.models import Asset, AssetCert, Department
    from job_manager.models import JobManifest
    from job_reports.models import JobReport
    from personnel.models import Personnel, PersonnelCert
    from sgs_report.models import SGSReport
    from sqb.models import SQBPackage

    today = datetime.date.today()

    equipment = _health(AssetCert.objects.filter(not_applicable=False), "expiry_date")
    people = _health(PersonnelCert.objects.filter(personnel__status="Active"), "expiry_date")
    attention, attention_total = _attention_items(today)

    jobs_by_status = dict(JobManifest.objects.values_list("status").annotate(n=Count("pk")))
    reports_by_status = dict(SGSReport.objects.values_list("status").annotate(n=Count("pk")))
    actions_by_status = dict(Action.objects.values_list("status").annotate(n=Count("pk")))
    open_actions = Action.objects.exclude(status="Completed")
    overdue_actions = open_actions.filter(due_date__lt=today).count()

    total_assets = Asset.objects.count()
    at_workshop = Asset.objects.filter(current_location="Workshop").count()
    away = total_assets - at_workshop
    by_location = list(
        Asset.objects.exclude(current_location="Workshop").values_list("current_location")
        .annotate(n=Count("pk")).order_by("-n")[:5]
    )
    if at_workshop:
        by_location.insert(0, ("Workshop", at_workshop))

    packages_open = SQBPackage.objects.filter(status="Open").count()

    return {
        "today": today,
        "equipment": equipment, "equipment_donut": donut_gradient(equipment),
        "people": people, "people_donut": donut_gradient(people),
        "attention": attention, "attention_total": attention_total,
        "kpis": {
            "departments": Department.objects.count(),
            "assets": total_assets, "at_workshop": at_workshop, "away": away,
            "active_jobs": jobs_by_status.get("Active", 0), "jobs_total": sum(jobs_by_status.values()),
            "open_actions": open_actions.count(), "overdue_actions": overdue_actions,
            "reports_open": reports_by_status.get("Open", 0), "reports_total": sum(reports_by_status.values()),
            "personnel_active": Personnel.objects.filter(status="Active").count(),
            "sqb_open": packages_open, "job_reports": JobReport.objects.count(),
        },
        "jobs_bars": _bars(
            [(s, jobs_by_status.get(s, 0)) for s in ("Draft", "Active", "Completed")],
            {"Draft": "var(--text-muted)", "Active": "var(--accent)", "Completed": "var(--success)"}),
        "reports_bars": _bars(
            [(s, reports_by_status.get(s, 0)) for s in ("Open", "Sent", "Approved")],
            {"Open": "var(--warning)", "Sent": "var(--accent)", "Approved": "var(--success)"}),
        "actions_bars": _bars(
            [(s, actions_by_status.get(s, 0)) for s in ("Pending", "In Progress", "Completed")],
            {"Pending": "var(--warning)", "In Progress": "var(--accent)", "Completed": "var(--success)"}),
        "location_bars": _bars(by_location),
        "recent_jobs": JobManifest.objects.order_by("-id")[:5],
        "recent_reports": SGSReport.objects.order_by("-created_at")[:5],
        "open_action_list": open_actions.order_by("due_date")[:5],
    }
