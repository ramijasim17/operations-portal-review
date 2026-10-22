import datetime

from django import forms
from django.contrib import messages
from django.db.models import ProtectedError
from django.http import Http404, HttpResponse
from django.utils.http import content_disposition_header
from django.shortcuts import get_object_or_404, redirect, render

from django.views.decorators.http import require_POST

from . import lists as shared_lists
from .evidence_pdf import combine_evidence_to_pdf
from .forms import ClientForm, FieldWellForm, WireDrumForm, WireLogEntryForm
from .models import Client, FieldWell, WireDrum, WireLogEntry, WireLogEvidence


def drum_list(request):
    drums = WireDrum.objects.all()
    q = request.GET.get("q")
    if q:
        drums = drums.filter(drum_number__icontains=q) | drums.filter(asset_serial_number__icontains=q)

    if request.method == "POST":
        form = WireDrumForm(request.POST)
        if form.is_valid():
            drum = form.save()
            messages.success(request, f"Drum '{drum.drum_number}' added.")
            return redirect("wire_management:list")
    else:
        form = WireDrumForm()

    return render(request, "wire_management/drum_list.html", {"drums": drums, "q": q or "", "form": form})


def drum_detail(request, pk):
    drum = get_object_or_404(WireDrum, pk=pk)
    if request.method == "POST":
        form = WireLogEntryForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.drum = drum
            entry.save()
            for f in request.FILES.getlist("files"):
                WireLogEvidence.objects.create(log_entry=entry, file=f)
            messages.success(request, "Log entry recorded.")
            return redirect("wire_management:detail", pk=drum.pk)
    else:
        form = WireLogEntryForm()

    return render(request, "wire_management/drum_detail.html", {
        "drum": drum, "form": form,
        "log_entries": drum.log_entries.select_related("client", "field_well", "supervisor").prefetch_related("evidence_files"),
    })


def drum_edit(request, pk):
    drum = get_object_or_404(WireDrum, pk=pk)
    if request.method == "POST":
        form = WireDrumForm(request.POST, instance=drum)
        if form.is_valid():
            form.save()
            messages.success(request, "Drum updated.")
            return redirect("wire_management:list")
    else:
        form = WireDrumForm(instance=drum)
    return render(request, "wire_management/drum_form.html", {"form": form, "drum": drum})


def drum_delete(request, pk):
    drum = get_object_or_404(WireDrum, pk=pk)
    if request.method == "POST":
        drum.delete()
        messages.success(request, "Drum deleted.")
        return redirect("wire_management:list")
    return render(request, "wire_management/drum_confirm_delete.html", {"drum": drum})


def entry_edit(request, pk):
    entry = get_object_or_404(WireLogEntry, pk=pk)
    if request.method == "POST":
        form = WireLogEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            for f in request.FILES.getlist("files"):
                WireLogEvidence.objects.create(log_entry=entry, file=f)
            messages.success(request, "Log entry updated.")
            return redirect("wire_management:detail", pk=entry.drum_id)
    else:
        form = WireLogEntryForm(instance=entry)
    return render(request, "wire_management/entry_form.html", {
        "form": form, "entry": entry, "evidence_files": entry.evidence_files.all(),
    })


def entry_delete(request, pk):
    entry = get_object_or_404(WireLogEntry, pk=pk)
    drum_pk = entry.drum_id
    if request.method == "POST":
        entry.delete()
        messages.success(request, "Log entry deleted.")
    return redirect("wire_management:detail", pk=drum_pk)


def evidence_delete(request, pk):
    evidence = get_object_or_404(WireLogEvidence, pk=pk)
    entry_pk = evidence.log_entry_id
    if request.method == "POST":
        evidence.delete()
        messages.success(request, "Evidence file deleted.")
    return redirect("wire_management:entry_edit", pk=entry_pk)


def drum_evidence_combined_pdf(request, pk):
    """Combines every evidence file across every job on this drum into
    one PDF, in date order -- for sending the whole wire's history to a
    client, or keeping one hard-copy-equivalent record on file."""
    drum = get_object_or_404(WireDrum, pk=pk)
    evidence_list = WireLogEvidence.objects.filter(log_entry__drum=drum).order_by(
        "log_entry__date", "log_entry__created_at", "uploaded_at")
    combined = combine_evidence_to_pdf(evidence_list)
    if combined is None:
        messages.error(request, "No evidence files on this drum yet.")
        return redirect("wire_management:detail", pk=drum.pk)
    response = HttpResponse(combined.read(), content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(True, f"{drum.drum_number}_evidence.pdf")
    return response


def entry_evidence_combined_pdf(request, pk):
    """Same as above, scoped to just one job entry."""
    entry = get_object_or_404(WireLogEntry, pk=pk)
    combined = combine_evidence_to_pdf(entry.evidence_files.order_by("uploaded_at"))
    if combined is None:
        messages.error(request, "No evidence files on this entry yet.")
        return redirect("wire_management:detail", pk=entry.drum_id)
    response = HttpResponse(combined.read(), content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(True, f"{entry.drum.drum_number}_{entry.date}_evidence.pdf")
    return response


def client_manage(request):
    clients = Client.objects.all()
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Client added.")
            return redirect("wire_management:client_manage")
    else:
        form = ClientForm()
    return render(request, "wire_management/client_manage.html", {"clients": clients, "form": form})


def client_delete(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if request.method == "POST":
        try:
            client.delete()
            messages.success(request, "Client deleted.")
        except ProtectedError:
            messages.error(request, f"Can't delete '{client.name}' \u2014 log entries still reference it.")
        return redirect("wire_management:client_manage")
    return render(request, "wire_management/client_confirm_delete.html", {"client": client})


def field_well_manage(request):
    fields = FieldWell.objects.all()
    if request.method == "POST":
        form = FieldWellForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Field/Well added.")
            return redirect("wire_management:field_well_manage")
    else:
        form = FieldWellForm()
    return render(request, "wire_management/field_well_manage.html", {"fields": fields, "form": form})


def field_well_delete(request, pk):
    field = get_object_or_404(FieldWell, pk=pk)
    if request.method == "POST":
        try:
            field.delete()
            messages.success(request, "Field/Well deleted.")
        except ProtectedError:
            messages.error(request, f"Can't delete '{field.name}' \u2014 log entries still reference it.")
        return redirect("wire_management:field_well_manage")
    return render(request, "wire_management/field_well_confirm_delete.html", {"field": field})


IMPORT_COLUMNS = [
    "Date (YYYY-MM-DD)", "Client", "Field/Well", "Sour Environment", "Wire Test Type",
    "Wire Test Results", "Max Depth", "Max Tension", "Jarring Event Depth",
    "Jarring Event Tension", "Jarring Event Duration", "Run Number",
    "Cut Length (ft)", "Remarks", "Supervisor Name",
]


def import_template_xlsx(request):
    """A blank spreadsheet with the exact columns the importer expects,
    so filling it in and re-uploading it always matches."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Wire Log Import"
    ws.append(IMPORT_COLUMNS)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = content_disposition_header(True, "wire_log_import_template.xlsx")
    wb.save(response)
    return response


def drum_import_excel(request, pk):
    """Bulk-imports job log entries for one drum from the template above.
    Client and Field/Well are created on the fly if new (matching the
    same managed-list pattern as their own dropdowns); Supervisor must
    match an existing active Personnel record by name, or is left blank
    with a note so nothing gets silently misattributed."""
    from personnel.models import Personnel

    drum = get_object_or_404(WireDrum, pk=pk)
    if request.method == "POST" and request.FILES.get("excel_file"):
        import openpyxl

        wb = openpyxl.load_workbook(request.FILES["excel_file"], data_only=True)
        ws = wb.active
        created, skipped = 0, []

        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or not row[0]:
                continue
            values = list(row) + [None] * (len(IMPORT_COLUMNS) - len(row))
            (date_val, client_name, field_name, sour_env, test_type, test_results,
             max_depth, max_tension, jarring_depth, jarring_tension, jarring_duration,
             run_number, cut_length, remarks, supervisor_name) = values[:15]

            if isinstance(date_val, datetime.datetime):
                date_val = date_val.date()
            elif isinstance(date_val, str):
                try:
                    date_val = datetime.datetime.strptime(date_val.strip(), "%Y-%m-%d").date()
                except ValueError:
                    skipped.append(f"Row {row_num}: couldn't parse date '{date_val}'")
                    continue
            elif not isinstance(date_val, datetime.date):
                skipped.append(f"Row {row_num}: missing or invalid date")
                continue

            client_obj = None
            if client_name:
                client_obj, _ = Client.objects.get_or_create(name=str(client_name).strip())
            field_obj = None
            if field_name:
                field_obj, _ = FieldWell.objects.get_or_create(name=str(field_name).strip())
            supervisor_obj = None
            if supervisor_name:
                supervisor_obj = Personnel.objects.filter(name__iexact=str(supervisor_name).strip()).first()
                if not supervisor_obj:
                    skipped.append(f"Row {row_num}: supervisor '{supervisor_name}' not found in Personnel, left blank")

            try:
                cut_length_val = float(cut_length) if cut_length not in (None, "") else 0
            except (TypeError, ValueError):
                cut_length_val = 0

            WireLogEntry.objects.create(
                drum=drum, date=date_val, client=client_obj, field_well=field_obj,
                sour_environment=sour_env or "", wire_test_type=test_type or "",
                wire_test_results=test_results or "", max_depth=max_depth or "",
                max_tension=max_tension or "", jarring_event_depth=jarring_depth or "",
                jarring_event_tension=jarring_tension or "", jarring_event_duration=jarring_duration or "",
                run_number=run_number or "", cut_length_ft=cut_length_val,
                remarks=remarks or "", supervisor=supervisor_obj,
            )
            created += 1

        msg = f"Imported {created} log entr{'y' if created == 1 else 'ies'}."
        if skipped:
            msg += " " + " | ".join(skipped[:6]) + (f" (+{len(skipped)-6} more)" if len(skipped) > 6 else "")
            messages.warning(request, msg)
        else:
            messages.success(request, msg)
        return redirect("wire_management:detail", pk=drum.pk)

    return render(request, "wire_management/import_excel.html", {"drum": drum})


# ── Shared Client / Field / Location lists ───────────────────────────────
class ListEntryForm(forms.Form):
    name = forms.CharField(max_length=150)

    def __init__(self, *args, model, exclude_pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.model, self.exclude_pk = model, exclude_pk

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        clash = self.model.objects.filter(name__iexact=name).exclude(pk=self.exclude_pk)
        if clash.exists():
            raise forms.ValidationError("That name is already in the list.")
        return name


def shared_lists_page(request):
    """One page to manage every dropdown list shared across the portal."""
    forms_by_kind = {}
    if request.method == "POST":
        kind = request.POST.get("kind")
        if kind not in shared_lists.KINDS:
            return redirect("wire_management:lists")
        model, label, _ = shared_lists.KINDS[kind]
        form = ListEntryForm(request.POST, model=model)
        if form.is_valid():
            model.objects.create(name=form.cleaned_data["name"])
            messages.success(request, f"'{form.cleaned_data['name']}' added to {label}.")
            return redirect("wire_management:lists")
        forms_by_kind[kind] = form
    sections = []
    for kind, (model, label, _) in shared_lists.KINDS.items():
        entries = [{"obj": o, "uses": shared_lists.usage_count(kind, o.name)} for o in model.objects.all()]
        sections.append({
            "kind": kind, "label": label, "entries": entries,
            "form": forms_by_kind.get(kind) or ListEntryForm(model=model),
        })
    return render(request, "wire_management/shared_lists.html", {"sections": sections})


def shared_list_edit(request, kind, pk):
    if kind not in shared_lists.KINDS:
        raise Http404
    model, label, _ = shared_lists.KINDS[kind]
    obj = get_object_or_404(model, pk=pk)
    form = ListEntryForm(request.POST or None, model=model, exclude_pk=obj.pk, initial={"name": obj.name})
    if request.method == "POST" and form.is_valid():
        old = obj.name
        shared_lists.rename(kind, obj, form.cleaned_data["name"])
        messages.success(request, f"Renamed '{old}' to '{obj.name}' everywhere it is used.")
        return redirect("wire_management:lists")
    return render(request, "wire_management/shared_list_edit.html", {
        "form": form, "obj": obj, "label": label, "uses": shared_lists.usage_count(kind, obj.name),
    })


@require_POST
def shared_list_delete(request, kind, pk):
    if kind not in shared_lists.KINDS:
        raise Http404
    model, label, _ = shared_lists.KINDS[kind]
    obj = get_object_or_404(model, pk=pk)
    try:
        obj.delete()
        messages.success(request, f"'{obj.name}' removed from {label}. Records that already use it keep the text.")
    except ProtectedError:
        messages.error(request, f"Can't delete '{obj.name}' \u2014 wire log entries still reference it.")
    return redirect("wire_management:lists")
