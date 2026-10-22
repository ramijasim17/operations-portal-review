import datetime
from collections import Counter

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from action_tracker.models import Action
from departments.models import Asset
from maintenance.models import MaintenanceRecord

from .forms import CorrectiveActionForm, IncidentForm, InspectionDoneForm, InspectionForm
from core.dept_scope import visible_or_404
from .scope import filter_qs, get_scope, initial_department
from .models import SEVERITY_CHOICES, SEVERITY_RANK, AuditTemplate, Incident, Inspection

def _asset_serials():
    return Asset.objects.values_list("name", flat=True)


def _asset_links(serial):
    """The Departments asset and its maintenance jobs, for the detail pages."""
    if not serial:
        return None, []
    return (Asset.objects.filter(name=serial).first(),
            list(MaintenanceRecord.objects.filter(asset_serial=serial, is_active=True)))


PRIORITY_FOR = {"Critical": "High", "High": "High", "Medium": "Medium", "Low": "Low"}


def _months_back(today, n):
    """First day of each of the last n months, oldest first."""
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(datetime.date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


# ───────── incidents ─────────
INCIDENT_TABS = ("open", "closed", "all")


def incident_list(request):
    tab = request.GET.get("tab", "open")
    if tab not in INCIDENT_TABS:
        tab = "open"
    q = request.GET.get("q", "").strip()
    severity = request.GET.get("severity", "")
    kind = request.GET.get("type", "")
    scope = get_scope(request)
    everything = filter_qs(Incident.objects.prefetch_related("actions").select_related("department"), scope)
    counts = {"open": everything.exclude(status=Incident.CLOSED).count(),
              "closed": everything.filter(status=Incident.CLOSED).count(), "all": everything.count()}
    rows = everything
    if tab == "open":
        rows = rows.exclude(status=Incident.CLOSED)
    elif tab == "closed":
        rows = rows.filter(status=Incident.CLOSED)
    if q:
        rows = rows.filter(Q(title__icontains=q) | Q(reference__icontains=q) | Q(location__icontains=q) | Q(client__icontains=q))
    if severity:
        rows = rows.filter(severity=severity)
    if kind:
        rows = rows.filter(incident_type=kind)
    rows = list(rows)
    if tab == "open":
        rows.sort(key=lambda i: (SEVERITY_RANK.get(i.severity, 9), -i.date.toordinal()))
    return render(request, "qhse/incident_list.html", {
        "section": "incidents", "scope": scope, "incidents": rows, "counts": counts, "tab": tab, "q": q, "severity": severity,
        "kind": kind, "severity_choices": SEVERITY_CHOICES, "type_choices": Incident.TYPE_CHOICES,
    })


def incident_create(request):
    form = IncidentForm(request.POST or None, request.FILES or None, initial={"department": initial_department(request)}, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        incident = form.save(commit=False)
        incident.reported_by = request.user
        incident.save()
        messages.success(request, f"{incident.reference} reported.")
        return redirect("qhse:incident_detail", pk=incident.pk)
    return render(request, "qhse/incident_form.html", {"form": form, "asset_serials": _asset_serials()})


def incident_edit(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    visible_or_404(request, incident.department_id)
    form = IncidentForm(request.POST or None, request.FILES or None, instance=incident, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Report updated.")
        return redirect("qhse:incident_detail", pk=pk)
    return render(request, "qhse/incident_form.html", {"form": form, "incident": incident, "asset_serials": _asset_serials()})


def incident_detail(request, pk):
    incident = get_object_or_404(Incident.objects.prefetch_related("actions"), pk=pk)
    visible_or_404(request, incident.department_id)
    asset, jobs = _asset_links(incident.asset_serial)
    return render(request, "qhse/incident_detail.html", {"section": "incidents", "incident": incident,
                                                         "linked": list(incident.actions.all()), "asset": asset, "jobs": jobs})


@require_POST
def incident_set_status(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    visible_or_404(request, incident.department_id)
    status = request.POST.get("status", "")
    if status in dict(Incident.STATUS_CHOICES):
        incident.status = status
        incident.save()
        messages.success(request, f"{incident.reference} is now {status.lower()}.")
    return redirect("qhse:incident_detail", pk=pk)


def incident_delete(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    visible_or_404(request, incident.department_id)
    if request.method == "POST":
        incident.delete()
        messages.success(request, "Deleted.")
    return redirect("qhse:incident_list")


def _raise_action(request, obj, ref_url, back, severity=None, client=""):
    form = CorrectiveActionForm(request.POST or None,
                                initial={"action_item": f"[{obj.reference}] ", "priority": PRIORITY_FOR.get(severity, "Medium")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        action = Action.objects.create(
            department_id=obj.department_id,
            action_item=d["action_item"], assignee=d["assignee"], priority=d["priority"], due_date=d["due_date"],
            client=client, action_type="procedure", raised_date=timezone.localdate(),
            comments=f"Corrective action from QHSE {obj.reference}: {obj.title}")
        obj.actions.add(action)
        messages.success(request, "Corrective action created. Follow it up in the Action Tracker.")
        return redirect(back, pk=obj.pk)
    return render(request, "qhse/action_form.html", {"form": form, "obj": obj, "back": back})


def incident_raise_action(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    visible_or_404(request, incident.department_id)
    return _raise_action(request, incident, None, "qhse:incident_detail", incident.severity, incident.client)


# ───────── inspections ─────────
INSPECTION_TABS = ("scheduled", "overdue", "done", "all")


def inspection_list(request):
    tab = request.GET.get("tab", "scheduled")
    if tab not in INSPECTION_TABS:
        tab = "scheduled"
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("type", "")
    scope = get_scope(request)
    everything = list(filter_qs(Inspection.objects.prefetch_related("actions").select_related("department"), scope))
    counts = {
        "scheduled": sum(not i.is_done for i in everything),
        "overdue": sum(i.status == "overdue" for i in everything),
        "done": sum(i.is_done for i in everything), "all": len(everything),
    }
    rows = everything
    if tab == "scheduled":
        rows = [i for i in rows if not i.is_done]
    elif tab == "overdue":
        rows = [i for i in rows if i.status == "overdue"]
    elif tab == "done":
        rows = [i for i in rows if i.is_done]
    if q:
        n = q.lower()
        rows = [i for i in rows if n in i.title.lower() or n in i.reference.lower() or n in i.area.lower()]
    if kind:
        rows = [i for i in rows if i.kind == kind]
    if tab == "done":
        rows.sort(key=lambda i: i.done_date, reverse=True)
    else:
        rows.sort(key=lambda i: (i.due_date is None, i.due_date or datetime.date.max))
    return render(request, "qhse/inspection_list.html", {
        "section": "inspections", "scope": scope, "inspections": rows, "counts": counts, "tab": tab, "q": q, "kind": kind,
        "kind_choices": Inspection.KIND_CHOICES,
    })


def inspection_create(request):
    initial = {"department": initial_department(request)}
    if request.GET.get("template", "").isdigit():
        tpl = AuditTemplate.objects.filter(pk=request.GET["template"]).first()
        if tpl:
            initial.update({"template": tpl, "title": tpl.name, "kind": tpl.kind})
            if tpl.department_id:
                initial["department"] = tpl.department
    form = InspectionForm(request.POST or None, request.FILES or None, initial=initial, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        item = form.save()
        messages.success(request, f"{item.reference} scheduled.")
        return redirect("qhse:inspection_list")
    return render(request, "qhse/inspection_form.html", {"form": form, "asset_serials": _asset_serials()})


def inspection_edit(request, pk):
    item = get_object_or_404(Inspection, pk=pk)
    visible_or_404(request, item.department_id)
    form = InspectionForm(request.POST or None, request.FILES or None, instance=item, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Updated.")
        return redirect("qhse:inspection_detail", pk=pk)
    return render(request, "qhse/inspection_form.html", {"form": form, "item": item, "asset_serials": _asset_serials()})


def inspection_detail(request, pk):
    item = get_object_or_404(Inspection.objects.prefetch_related("actions"), pk=pk)
    visible_or_404(request, item.department_id)
    asset, jobs = _asset_links(item.asset_serial)
    answers = list(item.answers.all())
    parts = []
    for a in answers:
        if not parts or parts[-1]["title"] != a.part_title:
            parts.append({"title": a.part_title, "rows": []})
        parts[-1]["rows"].append(a)
    return render(request, "qhse/inspection_detail.html", {"section": "inspections", "item": item, "parts": parts,
                                                           "linked": list(item.actions.all()), "asset": asset, "jobs": jobs})


def inspection_done(request, pk):
    item = get_object_or_404(Inspection, pk=pk)
    visible_or_404(request, item.department_id)
    form = InspectionDoneForm(request.POST or None, request.FILES or None, inspection=item)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        nxt = item.complete(d["done_date"], d["result"], d["findings"], d["inspector"], d.get("file") or None)
        msg = f"{item.reference} recorded as done."
        if nxt:
            msg += f" Next one scheduled for {nxt.due_date:%d %b %Y}."
        messages.success(request, msg)
        if d["result"] in (Inspection.FAIL, Inspection.IMPROVE):
            messages.warning(request, "The result needs follow-up: raise a corrective action from the inspection page.")
        return redirect("qhse:inspection_detail", pk=pk)
    return render(request, "qhse/inspection_done.html", {"form": form, "item": item})


def inspection_delete(request, pk):
    item = get_object_or_404(Inspection, pk=pk)
    visible_or_404(request, item.department_id)
    if request.method == "POST":
        item.delete()
        messages.success(request, "Deleted.")
    return redirect("qhse:inspection_list")


def inspection_raise_action(request, pk):
    item = get_object_or_404(Inspection, pk=pk)
    visible_or_404(request, item.department_id)
    return _raise_action(request, item, None, "qhse:inspection_detail", "High" if item.result == Inspection.FAIL else "Medium")
