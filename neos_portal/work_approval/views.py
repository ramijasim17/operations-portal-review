from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .forms import WAFileTypeForm, WARecordForm, WARenewForm
from .models import WAFileType, WARecord


def record_list(request):
    file_type_id = request.GET.get("file_type", "")
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()

    everything = list(WARecord.objects.select_related("file_type"))
    counts = {"expired": 0, "soon": 0, "valid": 0, "missing": 0, "na": 0}
    per_type = {}
    for r in everything:
        counts[r.status] += 1
        per_type[r.file_type_id] = per_type.get(r.file_type_id, 0) + 1

    rows = everything
    if file_type_id:
        rows = [r for r in rows if str(r.file_type_id) == file_type_id]
    if status:
        rows = [r for r in rows if r.status == status]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r.details.lower() or needle in r.location_name.lower()
                or needle in r.notes.lower()]
    rows.sort(key=WARecord.urgency_key)

    file_types = [{"obj": t, "count": per_type.get(t.pk, 0)} for t in WAFileType.objects.all()]
    return render(request, "work_approval/record_list.html", {
        "records": rows, "counts": counts, "total": len(everything),
        "file_types": file_types, "selected_type": file_type_id, "status": status, "q": q,
        "status_choices": [(k, v) for k, v in WARecord.STATUS_LABELS.items()],
    })


def record_create(request):
    if request.method == "POST":
        form = WARecordForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Record added.")
            return redirect("work_approval:list")
    else:
        form = WARecordForm()
    return render(request, "work_approval/record_form.html", {"form": form})


def record_edit(request, pk):
    record = get_object_or_404(WARecord, pk=pk)
    if request.method == "POST":
        form = WARecordForm(request.POST, request.FILES, instance=record)
        if form.is_valid():
            form.save()
            return redirect("work_approval:list")
    else:
        form = WARecordForm(instance=record)
    return render(request, "work_approval/record_form.html", {"form": form, "record": record})


def record_renew(request, pk):
    """Start a new record from an expiring / expired one: same type, details and
    location, new expiry date and file. The old record stays as history."""
    old = get_object_or_404(WARecord, pk=pk)
    if request.method == "POST":
        form = WARenewForm(request.POST, request.FILES)
        if form.is_valid():
            new = form.save()
            messages.success(request, f"Renewed \u2014 new expiry {new.expiry_date:%d %b %Y}." if new.expiry_date else "Renewed.")
            return redirect("work_approval:list")
    else:
        form = WARenewForm(initial={
            "file_type": old.file_type_id, "details": old.details, "location_name": old.location_name,
        })
    return render(request, "work_approval/record_form.html", {"form": form, "renew_from": old})


def record_delete(request, pk):
    record = get_object_or_404(WARecord, pk=pk)
    if request.method == "POST":
        record.delete()
        messages.success(request, "Deleted.")
    return redirect("work_approval:list")


def file_type_manage(request):
    types = WAFileType.objects.all()
    if request.method == "POST":
        form = WAFileTypeForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("work_approval:file_type_manage")
    else:
        form = WAFileTypeForm()
    return render(request, "work_approval/file_type_manage.html", {"types": types, "form": form})


def file_type_delete(request, pk):
    ft = get_object_or_404(WAFileType, pk=pk)
    if request.method == "POST":
        ft.delete()
    return redirect("work_approval:file_type_manage")
