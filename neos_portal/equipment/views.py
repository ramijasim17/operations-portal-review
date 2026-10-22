"""
equipment/views.py — the Django equivalent of tab_certificates.py's render()
and its dialogs. Same underlying logic (flat one-row-per-cert records,
status buckets, filters), expressed as Django views + templates instead of
Streamlit widgets.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from .forms import EquipmentCertForm, EquipmentForm
from .models import CategoryCertRequirement, Equipment, EquipmentCert


def equipment_list(request):
    equipment = Equipment.objects.all()
    category = request.GET.get("category")
    q = request.GET.get("q")
    if category:
        equipment = equipment.filter(category=category)
    if q:
        equipment = equipment.filter(serial_number__icontains=q)
    return render(request, "equipment/equipment_list.html", {
        "equipment": equipment,
        "categories": Equipment._meta.get_field("category").choices,
    })


def cert_list(request):
    """Flat table: one row per (equipment, cert type) — same shape as the
    Streamlit tab's main table."""
    certs = EquipmentCert.objects.select_related("equipment", "cert_type").all()

    q = request.GET.get("q")
    status = request.GET.get("status")
    cert_type = request.GET.get("cert_type")

    if q:
        certs = certs.filter(equipment__serial_number__icontains=q)
    if cert_type:
        certs = certs.filter(cert_type__name=cert_type)

    rows = list(certs)
    if status:
        rows = [c for c in rows if c.status == status]

    counts = {"expired": 0, "soon": 0, "valid": 0, "missing": 0}
    for c in certs:
        if c.status in counts:
            counts[c.status] += 1

    return render(request, "equipment/cert_list.html", {
        "rows": rows,
        "counts": counts,
        "total": certs.count(),
    })


@role_required("admin", "approver", "editor")
def cert_edit(request, cert_id):
    cert = get_object_or_404(EquipmentCert, id=cert_id)
    if request.method == "POST":
        form = EquipmentCertForm(request.POST, request.FILES, instance=cert)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.uploaded_by = request.user
            obj.save()
            messages.success(request, "Saved.")
            return redirect("equipment:cert_list")
    else:
        form = EquipmentCertForm(instance=cert)
    return render(request, "equipment/cert_edit.html", {"form": form, "cert": cert})


@role_required("admin", "approver", "editor")
def equipment_create(request):
    if request.method == "POST":
        form = EquipmentForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Equipment added.")
            return redirect("equipment:list")
    else:
        form = EquipmentForm()
    return render(request, "equipment/equipment_form.html", {"form": form})
