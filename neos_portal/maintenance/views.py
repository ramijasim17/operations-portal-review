import datetime

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from core.dept_scope import asset_department_map, asset_serials_for, filter_by_serial, get_scope, serial_visible_or_404
from departments.models import Asset
from wire_management.evidence_pdf import combine_evidence_to_pdf

from .forms import CompleteForm, FailureForm, MaintenanceLevelForm, MaintenanceRecordForm
from .models import MaintenanceLevel, MaintenanceLog, MaintenanceRecord

TABS = ("all", "levelled", "repeating", "other", "pressure", "done")


def _tab_filter(records, tab):
    if tab == "done":
        return records.filter(is_active=False)
    records = records.filter(is_active=True)
    if tab == "levelled":
        return records.filter(schedule=MaintenanceRecord.LEVELLED, activity=MaintenanceRecord.MAINTENANCE)
    if tab == "repeating":
        return records.filter(schedule=MaintenanceRecord.REPEATING, activity=MaintenanceRecord.MAINTENANCE)
    if tab == "other":
        return records.filter(
            schedule__in=(MaintenanceRecord.ONE_OFF, MaintenanceRecord.ON_FAILURE),
            activity=MaintenanceRecord.MAINTENANCE,
        )
    if tab == "pressure":
        return records.filter(activity=MaintenanceRecord.PRESSURE_TEST)
    return records


def record_list(request):
    tab = request.GET.get("tab", "all")
    if tab not in TABS:
        tab = "all"
    q = request.GET.get("q", "").strip()

    scope = get_scope(request)
    everything = filter_by_serial(MaintenanceRecord.objects.select_related("maintenance_level"), scope)
    tab_counts = {t: _tab_filter(everything, t).count() for t in TABS}

    records = _tab_filter(everything, tab)
    if q:
        records = records.filter(Q(asset_serial__icontains=q) | Q(location_name__icontains=q))
    # Most urgent first: overdue, then soonest; standby / no-date last.
    rows = sorted(records, key=lambda r: (r.due_date is None, r.due_date or datetime.date.max))

    counts = {"expired": 0, "soon": 0, "valid": 0, "standby": 0}
    for r in _tab_filter(everything, "all"):
        if r.status in counts:
            counts[r.status] += 1
        elif r.status == "missing":
            counts["standby"] += 1
    descriptions = dict(Asset.objects.filter(name__in=[r.asset_serial for r in rows]).values_list("name", "description"))
    for r in rows:
        r.asset_description = descriptions.get(r.asset_serial, "")
        r.asset_known = r.asset_serial in descriptions

    return render(request, "maintenance/record_list.html", {
        "records": rows, "counts": counts, "q": q, "tab": tab, "tab_counts": tab_counts, "scope": scope,
        "asset_serials": _serials(scope),
    })


def _serials(scope):
    allowed = asset_serials_for(scope)
    return sorted(allowed) if allowed is not None else list(Asset.objects.values_list("name", flat=True))


def _form_context(form, record=None, scope=None):
    return {
        "form": form, "record": record,
        "asset_serials": _serials(scope) if scope else Asset.objects.values_list("name", flat=True),
        "levels": {str(l.pk): l.interval_text for l in MaintenanceLevel.objects.all()},
    }


def record_create(request):
    scope = get_scope(request)
    allowed = asset_serials_for(scope) if scope.locked else None
    if request.method == "POST":
        form = MaintenanceRecordForm(request.POST, request.FILES, allowed=allowed)
        if form.is_valid():
            record = form.save()
            messages.success(request, f"Added: {record.asset_serial} — {record.schedule_text}.")
            return redirect("maintenance:list")
    else:
        form = MaintenanceRecordForm(initial={"activity": request.GET.get("activity", "maintenance")}, allowed=allowed)
    return render(request, "maintenance/record_form.html", _form_context(form, scope=scope))


def record_edit(request, pk):
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    scope = get_scope(request)
    allowed = asset_serials_for(scope) if scope.locked else None
    if request.method == "POST":
        form = MaintenanceRecordForm(request.POST, request.FILES, instance=record, allowed=allowed)
        if form.is_valid():
            form.save()
            messages.success(request, "Maintenance record updated.")
            return redirect("maintenance:list")
    else:
        form = MaintenanceRecordForm(instance=record, allowed=allowed)
    return render(request, "maintenance/record_form.html", _form_context(form, record, scope))


def record_delete(request, pk):
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    if request.method == "POST":
        record.delete()
        messages.success(request, "Deleted.")
    return redirect("maintenance:list")


def record_complete(request, pk):
    """Mark the job as done: log it and move the next due date on."""
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    form = CompleteForm(request.POST or None, request.FILES or None, record=record)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        result = data.get("result", "")
        log = record.complete(data["done_date"], user=request.user, notes=data["notes"],
                              file=data.get("file") or None, result=result)
        follow_up = record.raise_follow_up("Failed test") if result == "Fail" else None
        if follow_up:
            messages.warning(request, f"A follow-up action was added to the Action Tracker: {follow_up.action_item}")
        from approvals import services as approvals
        if not approvals.is_approved(log, "maintenance_log"):
            messages.info(request, f"{record.asset_serial}: report saved and sent to a Level 3 for approval. "
                                   "The schedule moves on once it is approved.")
        elif result == "Fail":
            messages.warning(request, f"{record.asset_serial}: pressure test recorded as FAILED.")
        elif record.due_date:
            messages.success(request, f"{record.asset_serial} marked done. Next due {record.due_date:%d %b %Y}.")
        else:
            messages.success(request, f"{record.asset_serial} marked done.")
        return redirect("maintenance:list")
    return render(request, "maintenance/record_complete.html", {"form": form, "record": record})


def record_failure(request, pk):
    """The asset failed: the job becomes due today."""
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    form = FailureForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        record.report_failure(form.cleaned_data["date"], user=request.user, notes=form.cleaned_data["notes"])
        messages.warning(request, f"Failure logged for {record.asset_serial}. It is now due for maintenance.")
        if record.raise_follow_up("Failure reported"):
            messages.warning(request, "A follow-up action was added to the Action Tracker.")
        return redirect("maintenance:list")
    return render(request, "maintenance/record_failure.html", {"form": form, "record": record})


def record_history(request, pk):
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    from qhse.models import Incident, Inspection

    return render(request, "maintenance/record_history.html", {
        "record": record, "logs": record.logs.select_related("by").prefetch_related("approvals"),
        "asset": record.asset(), "linked_actions": list(record.actions.all()),
        "qhse_inspections": list(Inspection.objects.filter(asset_serial=record.asset_serial)[:8]),
        "qhse_incidents": list(Incident.objects.filter(asset_serial=record.asset_serial)[:8]),
    })


def record_action(request, pk):
    """Raise an action in the Action Tracker for this job, linked back to it."""
    from django.utils import timezone

    from action_tracker.forms import RaiseActionForm
    from action_tracker.models import Action

    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    form = RaiseActionForm(request.POST or None, initial={"action_item": f"{record.asset_serial}: "})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        action = Action.objects.create(
            department_id=asset_department_map([record.asset_serial]).get(record.asset_serial),
            action_item=d["action_item"], assignee=d["assignee"], priority=d["priority"], due_date=d["due_date"],
            action_type="equipment", raised_date=timezone.localdate(),
            comments=f"Raised from Maintenance for {record.asset_serial} ({record.get_activity_display()}).")
        record.actions.add(action)
        messages.success(request, "Action created. Follow it up in the Action Tracker.")
        return redirect("maintenance:history", pk=pk)
    return render(request, "maintenance/record_action.html", {"form": form, "record": record})


def record_history_pdf(request, pk):
    """Every report attached to this job's history, oldest first, merged
    into one PDF (photos and PDFs; other file types are skipped)."""
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    serial_visible_or_404(request, record.asset_serial)
    logs = record.logs.exclude(file="").exclude(file__isnull=True).order_by("done_date", "id")
    combined = combine_evidence_to_pdf(logs)
    if combined is None:
        messages.error(request, "There are no PDF or photo reports in this history to combine.")
        return redirect("maintenance:history", pk=record.pk)
    response = HttpResponse(combined.read(), content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(True, f"{record.asset_serial}_reports.pdf")
    return response


def level_manage(request):
    levels = MaintenanceLevel.objects.all()
    if request.method == "POST":
        form = MaintenanceLevelForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Level added.")
            return redirect("maintenance:level_manage")
    else:
        form = MaintenanceLevelForm()
    return render(request, "maintenance/level_manage.html", {"levels": levels, "form": form})


def level_edit(request, pk):
    level = get_object_or_404(MaintenanceLevel, pk=pk)
    form = MaintenanceLevelForm(request.POST or None, instance=level)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{level.level_name} updated.")
        return redirect("maintenance:level_manage")
    return render(request, "maintenance/level_edit.html", {"form": form, "level": level})


def level_delete(request, pk):
    level = get_object_or_404(MaintenanceLevel, pk=pk)
    if request.method == "POST":
        level.delete()
    return redirect("maintenance:level_manage")
