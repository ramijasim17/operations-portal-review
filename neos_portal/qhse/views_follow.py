"""Follow-up: one place that watches Maintenance and the Action Tracker, and the investigations opened from them."""
import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from action_tracker.models import Action
from departments.models import Asset
from maintenance.models import MaintenanceLog, MaintenanceRecord

from .forms import CorrectiveActionForm, InvestigationForm
from .models import Incident, Inspection, Investigation
from core.dept_scope import filter_by_serial, filter_by_unit, serial_visible_or_404, visible_or_404
from .scope import asset_department_map, filter_qs, get_scope, initial_department


def failed_records(records):
    """Jobs whose last test failed, or that had a failure reported and have not been done since."""
    last_failure = {}
    for log in MaintenanceLog.objects.filter(event=MaintenanceLog.FAILURE).order_by("done_date"):
        last_failure[log.record_id] = log.done_date
    out = []
    for r in records:
        reported = last_failure.get(r.pk)
        if r.last_result == "Fail" or (reported and (r.last_done is None or reported >= r.last_done)):
            out.append(r)
    return out


def dashboard(request):
    today = timezone.localdate()
    scope = get_scope(request)
    records = list(filter_by_serial(MaintenanceRecord.objects.filter(is_active=True), scope))
    overdue_jobs = sorted([r for r in records if r.status == "expired"], key=lambda r: r.due_date)
    failed = failed_records(records)
    overdue_actions = sorted([a for a in filter_qs(Action.objects.exclude(status="Completed"), scope) if a.is_overdue], key=lambda a: a.due_date)
    inspections = [i for i in filter_qs(Inspection.objects.all(), scope) if not i.is_done]
    overdue_inspections = sorted([i for i in inspections if i.status == "overdue"], key=lambda i: i.due_date)
    open_investigations = list(filter_qs(Investigation.objects.exclude(status=Investigation.CLOSED).select_related("department"), scope))

    by_job = {i.maintenance_record_id: i for i in open_investigations if i.maintenance_record_id}
    by_action = {i.action_id: i for i in open_investigations if i.action_id}
    descriptions = dict(Asset.objects.filter(name__in=[r.asset_serial for r in overdue_jobs + failed]).values_list("name", "description"))
    for r in overdue_jobs + failed:
        r.inv = by_job.get(r.pk)
        r.asset_description = descriptions.get(r.asset_serial, "")
    for a in overdue_actions:
        a.inv = by_action.get(a.pk)

    # non-productive time: recent entries, biggest first, with a button to look into each
    from sqb.models import SQBTrackerEntry

    since = today - datetime.timedelta(days=60)
    npt_all = filter_by_unit(SQBTrackerEntry.objects.filter(date__gte=since, npt_hours__gt=0).select_related("unit", "job_report"), scope)
    by_npt = {i.npt_entry_id: i for i in Investigation.objects.filter(npt_entry__isnull=False)}
    npt_entries = sorted(npt_all, key=lambda e: -(e.npt_hours or 0))
    for e in npt_entries:
        e.inv = by_npt.get(e.pk)
    npt_month = sum(e.npt_hours or 0 for e in npt_entries if e.date >= today.replace(day=1))

    incidents_in_scope = filter_qs(Incident.objects.all(), scope)
    serious = [i for i in incidents_in_scope if i.incident_type in (Incident.INCIDENT, Incident.INJURY)]
    last_serious = max((i.date for i in serious), default=None)
    open_incidents = incidents_in_scope.exclude(status=Incident.CLOSED)
    stats = {
        "overdue_jobs": len(overdue_jobs), "failed": len(failed), "overdue_actions": len(overdue_actions),
        "open_investigations": len(open_investigations), "overdue_inspections": len(overdue_inspections),
        "open_incidents": open_incidents.count(), "serious_open": open_incidents.filter(severity__in=("High", "Critical")).count(),
        "days_since": (today - last_serious).days if last_serious else None,
        "npt_month": round(npt_month, 1), "npt_open": sum(1 for e in npt_entries if not e.inv),
    }
    return render(request, "qhse/dashboard.html", {
        "section": "followup", "scope": scope, "stats": stats, "overdue_jobs": overdue_jobs, "failed": failed,
        "overdue_actions": overdue_actions, "overdue_inspections": overdue_inspections[:6], "npt_entries": npt_entries[:8],
        "investigations": sorted(open_investigations, key=lambda i: (i.due_date is None, i.due_date or datetime.date.max))[:6],
    })


# ───────── investigations ─────────
TABS = ("open", "closed", "all")


def investigation_list(request):
    tab = request.GET.get("tab", "open")
    if tab not in TABS:
        tab = "open"
    q = request.GET.get("q", "").strip()
    scope = get_scope(request)
    everything = filter_qs(Investigation.objects.prefetch_related("actions").select_related("department"), scope)
    counts = {"open": everything.exclude(status="Closed").count(), "closed": everything.filter(status="Closed").count(), "all": everything.count()}
    rows = everything
    if tab == "open":
        rows = rows.exclude(status="Closed")
    elif tab == "closed":
        rows = rows.filter(status="Closed")
    if q:
        from django.db.models import Q
        rows = rows.filter(Q(title__icontains=q) | Q(reference__icontains=q) | Q(asset_serial__icontains=q))
    return render(request, "qhse/investigation_list.html", {"section": "investigations", "scope": scope, "rows": list(rows), "counts": counts, "tab": tab, "q": q})


def _prefill(request):
    """Fill a new investigation from the job, action or incident it was opened from."""
    g = request.GET
    today = timezone.localdate()
    if g.get("maintenance"):
        r = get_object_or_404(MaintenanceRecord, pk=g["maintenance"])
        serial_visible_or_404(request, r.asset_serial)
        failed = g.get("trigger") == "failed_test" or r.last_result == "Fail"
        facts = [f"{r.get_activity_display()} for {r.asset_serial}, {r.schedule_text}."]
        if r.due_date:
            facts.append(f"Due {r.due_date:%d %b %Y}" + (f" ({(today - r.due_date).days} days overdue)." if r.due_date < today else "."))
        if r.last_done:
            facts.append(f"Last done {r.last_done:%d %b %Y}" + (f", result {r.last_result}." if r.last_result else "."))
        trig = g.get("trigger") or (Investigation.FAILED_TEST if failed else Investigation.OVERDUE_JOB if r.status == "expired" else Investigation.FAILURE)
        title = {"failed_test": "Failed test", "overdue_job": "Overdue maintenance"}.get(trig, "Equipment failure")
        return {"title": f"{title}: {r.asset_serial}", "trigger": trig, "asset_serial": r.asset_serial,
                "department": asset_department_map([r.asset_serial]).get(r.asset_serial),
                "what_happened": " ".join(facts), "maintenance_record": r}
    if g.get("action"):
        a = get_object_or_404(Action, pk=g["action"])
        visible_or_404(request, a.department_id)
        late = f" It was due {a.due_date:%d %b %Y}, {(today - a.due_date).days} days ago." if a.due_date and a.due_date < today else ""
        return {"title": f"Overdue action: {a.action_item}"[:200], "trigger": Investigation.OVERDUE_ACTION, "owner": a.assignee,
                "department": a.department_id,
                "what_happened": f"Action \"{a.action_item}\", assigned to {a.assignee or 'nobody'}.{late}", "action": a}
    if g.get("incident"):
        i = get_object_or_404(Incident, pk=g["incident"])
        visible_or_404(request, i.department_id)
        return {"title": f"Investigation: {i.reference} {i.title}"[:200], "trigger": Investigation.INCIDENT,
                "asset_serial": i.asset_serial, "owner": i.assigned_to, "what_happened": i.description, "incident": i,
                "department": i.department_id}
    if g.get("npt"):
        from sqb.models import SQBTrackerEntry

        e = get_object_or_404(SQBTrackerEntry.objects.select_related("unit__classification", "job_report"), pk=g["npt"])
        dept_id = e.unit.classification.department_id if e.unit_id else None
        visible_or_404(request, dept_id)
        hours = f"{e.npt_hours:g}" if e.npt_hours else "?"
        cause = e.get_category_display() if e.category else "cause not recorded"
        unit = e.unit.name if e.unit_id else "no unit"
        facts = [f"{hours} h of non-productive time on {e.date:%d %b %Y}" if e.date else f"{hours} h of non-productive time",
                 f"unit {unit}", f"cause: {cause}"]
        if e.job_report_id:
            facts.append(f"job {e.job_report.name}")
        if e.client:
            facts.append(f"client {e.client}")
        text = ", ".join(facts) + "."
        if e.description:
            text += f" Recorded: {e.description}"
        return {"title": f"NPT: {unit}, {hours} h ({cause})"[:200], "trigger": Investigation.NPT,
                "asset_serial": e.unit.name if e.unit_id else "", "department": dept_id, "what_happened": text, "npt_entry": e}
    return {}


def investigation_create(request):
    pre = _prefill(request)
    if not pre.get("department"):
        pre["department"] = initial_department(request)
    elif isinstance(pre["department"], int):
        from departments.models import Department
        pre["department"] = Department.objects.filter(pk=pre["department"]).first()
    links = {k: pre.pop(k) for k in ("maintenance_record", "action", "incident", "npt_entry") if k in pre}
    form = InvestigationForm(request.POST or None, initial=pre, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        inv = form.save(commit=False)
        inv.created_by = request.user
        for k, v in links.items():
            setattr(inv, k, v)
        if not links:                                         # keep the link when the form was posted without the query string
            for k, model in (("maintenance_record", MaintenanceRecord), ("action", Action), ("incident", Incident)):
                pk = request.POST.get(f"link_{k}")
                if pk:
                    setattr(inv, k, model.objects.filter(pk=pk).first())
        inv.save()
        messages.success(request, f"{inv.reference} opened.")
        return redirect("qhse:investigation_detail", pk=inv.pk)
    return render(request, "qhse/investigation_form.html", {"form": form, "links": links, "asset_serials": Asset.objects.values_list("name", flat=True)})


def investigation_edit(request, pk):
    inv = get_object_or_404(Investigation, pk=pk)
    visible_or_404(request, inv.department_id)
    form = InvestigationForm(request.POST or None, instance=inv, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Investigation updated.")
        return redirect("qhse:investigation_detail", pk=pk)
    return render(request, "qhse/investigation_form.html", {"form": form, "inv": inv, "links": {}, "asset_serials": Asset.objects.values_list("name", flat=True)})


def investigation_detail(request, pk):
    inv = get_object_or_404(Investigation.objects.select_related("maintenance_record", "action", "incident").prefetch_related("actions"), pk=pk)
    visible_or_404(request, inv.department_id)
    return render(request, "qhse/investigation_detail.html", {"section": "investigations", "inv": inv, "linked": list(inv.actions.all())})


@require_POST
def investigation_status(request, pk):
    inv = get_object_or_404(Investigation, pk=pk)
    visible_or_404(request, inv.department_id)
    status = request.POST.get("status", "")
    if status in dict(Investigation.STATUS_CHOICES):
        inv.status = status
        inv.save()
        messages.success(request, f"{inv.reference} is now {status.lower()}.")
    return redirect("qhse:investigation_detail", pk=pk)


def investigation_delete(request, pk):
    inv = get_object_or_404(Investigation, pk=pk)
    visible_or_404(request, inv.department_id)
    if request.method == "POST":
        inv.delete()
        messages.success(request, "Deleted.")
    return redirect("qhse:investigation_list")


def investigation_raise_action(request, pk):
    inv = get_object_or_404(Investigation, pk=pk)
    visible_or_404(request, inv.department_id)
    form = CorrectiveActionForm(request.POST or None, initial={"action_item": f"[{inv.reference}] ", "priority": "High"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        action = Action.objects.create(
            department_id=inv.department_id,
            action_item=d["action_item"], assignee=d["assignee"], priority=d["priority"], due_date=d["due_date"],
            action_type="equipment" if inv.asset_serial else "procedure", raised_date=timezone.localdate(),
            comments=f"Corrective action from investigation {inv.reference}: {inv.title}")
        inv.actions.add(action)
        if inv.maintenance_record_id:
            inv.maintenance_record.actions.add(action)
        messages.success(request, "Corrective action created. Follow it up in the Action Tracker.")
        return redirect("qhse:investigation_detail", pk=pk)
    return render(request, "qhse/action_form.html", {"form": form, "obj": inv, "back": "qhse:investigation_detail"})
