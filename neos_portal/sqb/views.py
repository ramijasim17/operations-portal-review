import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    ClientNPTSettingForm,
    unit_queryset,
    NPTKPIForm,
    SQBDocumentForm,
    SQBJobForm,
    SQBPackageCreateForm,
    SQBPartDefinitionForm,
    SQBPartUploadForm,
    SQBTrackerEntryForm,
    SQBTypeForm,
)
from .models import (
    NPT_CATEGORY_CHOICES,
    SQBDocument,
    SQBJob,
    SQBPackage,
    SQBPartDefinition,
    SQBPartUpload,
    SQBTrackerEntry,
    SQBType,
)


def package_job_label(job_report):
    """The label stored on a package: job name + its manifest number, so two
    jobs with the same name under different manifests stay distinct."""
    return f"{job_report.name} \u00b7 {job_report.manifest.job_number}"[:255]


def job_list(request):
    """The SQB dashboard \u2014 three tabs: Missing SQB (jobs from Job Reports
    that have no package yet), Open, and Closed (download the merged PDF)."""
    from job_reports.models import JobReport

    tab = request.GET.get("tab", "missing")

    missing_jobs = (
        JobReport.objects.filter(sqb_packages__isnull=True)
        .select_related("manifest", "linked_sgs_report").order_by("-created_at")
    )

    from django.db.models import Count, Q

    open_packages = list(
        SQBPackage.objects.filter(status="Open").select_related("sqb_type", "job_report__manifest")
        .annotate(parts_total=Count("parts"), parts_done=Count("parts", filter=~Q(parts__file="")))
    )
    for pkg in open_packages:
        pkg.percent = round(100 * pkg.parts_done / pkg.parts_total) if pkg.parts_total else 0
    ready = sum(1 for p in open_packages if p.parts_total and p.parts_done == p.parts_total)

    context = {
        "tab": tab,
        "missing_jobs": missing_jobs,
        "open_packages": open_packages,
        "closed_packages": SQBPackage.objects.filter(status="Closed").select_related("sqb_type", "closed_by"),
        "sqb_types": SQBType.objects.all(),
        "ready_to_close": ready,
    }
    return render(request, "sqb/dashboard.html", context)


def package_create(request, job_report_pk):
    from job_reports.models import JobReport

    job_report = get_object_or_404(JobReport.objects.select_related("manifest"), pk=job_report_pk)
    if request.method == "POST":
        form = SQBPackageCreateForm(request.POST)
        if form.is_valid():
            sqb_type = form.cleaned_data["sqb_type"]
            label = package_job_label(job_report)
            clash = SQBPackage.objects.filter(job_name=label, sqb_type=sqb_type).exclude(job_report=job_report)
            if clash.exists():  # same name under the same manifest: keep the label unique
                label = f"{label[:240]} (#{job_report.pk})"
            package, created = SQBPackage.objects.get_or_create(
                job_report=job_report, sqb_type=sqb_type, defaults={"job_name": label},
            )
            if created:
                for part_def in sqb_type.parts.all():
                    SQBPartUpload.objects.create(package=package, part_definition=part_def)
                messages.success(request, f"SQB created for '{job_report.name}'.")
            return redirect("sqb:package_detail", pk=package.pk)
    else:
        form = SQBPackageCreateForm()
    return render(request, "sqb/package_create.html", {"form": form, "job_report": job_report})


def package_detail(request, pk):
    package = get_object_or_404(SQBPackage, pk=pk)
    parts = package.parts.select_related("part_definition").all()

    if request.method == "POST" and "part_id" in request.POST:
        part = get_object_or_404(SQBPartUpload, pk=request.POST["part_id"], package=package)
        form = SQBPartUploadForm(request.POST, request.FILES, instance=part)
        if form.is_valid():
            from django.utils import timezone
            part = form.save(commit=False)
            part.uploaded_date = timezone.localdate()
            part.save()
            messages.success(request, f"Uploaded {part.part_definition.name}.")
        else:
            messages.error(request, "Only PDF files are accepted for SQB parts.")
        return redirect("sqb:package_detail", pk=pk)

    return render(request, "sqb/package_detail.html", {
        "package": package, "parts": parts,
    })


def fill_digital_form(request, part_pk):
    from .digital_forms import FORM_DEFINITIONS
    from .models import DigitalFormEntry

    part = get_object_or_404(SQBPartUpload, pk=part_pk)
    form_key = part.part_definition.digital_form_key
    definition = FORM_DEFINITIONS.get(form_key)
    if not definition:
        messages.error(request, "This part isn't set up as a digital form.")
        return redirect("sqb:package_detail", pk=part.package_id)

    entry, _ = DigitalFormEntry.objects.get_or_create(part_upload=part, defaults={"form_key": form_key})

    if request.method == "POST":
        header_data = {key: request.POST.get(f"header_{key}", "") for key, _, _ in definition["header_fields"]}

        items_data = {}
        if "items" in definition:  # single flat list (e.g. pass_fill)
            for i in range(len(definition["items"])):
                items_data[str(i)] = {
                    "status": request.POST.get(f"item_{i}_status", ""),
                }
        if "sections" in definition:
            for s_idx, section in enumerate(definition["sections"]):
                for i_idx in range(len(section["items"])):
                    key = f"{s_idx}_{i_idx}"
                    if section["type"] == "check_comment":
                        items_data[key] = {
                            "checked": request.POST.get(f"item_{key}_checked") == "on",
                            "comment": request.POST.get(f"item_{key}_comment", ""),
                        }
                    else:  # response_only
                        items_data[key] = {"response": request.POST.get(f"item_{key}_response", "")}

        entry.header_data = header_data
        entry.items_data = items_data
        entry.comments = request.POST.get("comments", "")
        entry.save()

        pdf_buffer = _generate_digital_form_pdf(definition, entry)
        from django.core.files.base import ContentFile
        part.file.save(f"{form_key}_{part.package_id}.pdf", ContentFile(pdf_buffer.read()), save=False)
        from django.utils import timezone
        part.uploaded_date = timezone.localdate()
        part.save()

        messages.success(request, f"{definition['title']} saved and generated.")
        return redirect("sqb:package_detail", pk=part.package_id)

    return render(request, "sqb/digital_form.html", {
        "part": part, "definition": definition, "entry": entry, "form_key": form_key,
    })


def _generate_digital_form_pdf(definition, entry):
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleC", parent=styles["Title"], fontSize=16)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, leading=11)

    story = [Paragraph(definition["title"], title_style)]
    if definition.get("form_ref"):
        story.append(Paragraph(definition["form_ref"], small))
    story.append(Spacer(1, 12))

    header_rows = []
    for key, label, _ in definition["header_fields"]:
        header_rows.append([label, entry.header_data.get(key, "")])
    header_table = Table(header_rows, colWidths=[2.2 * inch, 4.3 * inch])
    header_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    if "items" in definition and definition.get("item_type") == "pass_fill":
        cols = definition.get("status_columns", ["Pass", "Fill"])
        rows = [["#", "Description", cols[0], cols[1]]]
        for i, item in enumerate(definition["items"]):
            status = entry.items_data.get(str(i), {}).get("status", "")
            rows.append([str(i + 1), Paragraph(item, small), "\u2713" if status == "pass" else "", "\u2713" if status == "fill" else ""])
        t = Table(rows, colWidths=[0.3 * inch, 4.7 * inch, 0.7 * inch, 0.7 * inch])
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (2, 0), (3, -1), "CENTER"),
        ]))
        story.append(t)

    if "sections" in definition:
        for s_idx, section in enumerate(definition["sections"]):
            story.append(Spacer(1, 10))
            story.append(Paragraph(section["title"], styles["Heading3"]))
            if section["type"] == "check_comment":
                rows = [["#", "Description", "Check", "Comment"]]
                for i_idx, item in enumerate(section["items"]):
                    key = f"{s_idx}_{i_idx}"
                    d = entry.items_data.get(key, {})
                    rows.append([str(i_idx + 1), Paragraph(item, small),
                                 "\u2713" if d.get("checked") else "", Paragraph(d.get("comment", ""), small)])
                t = Table(rows, colWidths=[0.3 * inch, 3.3 * inch, 0.6 * inch, 2.2 * inch])
            else:
                rows = [["#", "Description", "Response"]]
                for i_idx, item in enumerate(section["items"]):
                    key = f"{s_idx}_{i_idx}"
                    d = entry.items_data.get(key, {})
                    rows.append([str(i_idx + 1), Paragraph(item, small), Paragraph(d.get("response", ""), small)])
                t = Table(rows, colWidths=[0.3 * inch, 3.9 * inch, 2.2 * inch])
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 0), (2, -1), "CENTER" if section["type"] == "check_comment" else "LEFT"),
            ]))
            story.append(t)

    if entry.comments:
        story.append(Spacer(1, 14))
        story.append(Paragraph("Comments:", styles["Heading3"]))
        story.append(Paragraph(entry.comments, small))

    doc.build(story)
    buf.seek(0)
    return buf


def package_delete(request, pk):
    package = get_object_or_404(SQBPackage, pk=pk)
    if request.method == "POST":
        package.delete()
        messages.success(request, "SQB package deleted.")
        return redirect("sqb:job_list")
    return redirect("sqb:package_detail", pk=pk)


def package_close(request, pk):
    import io

    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from django.utils import timezone

    package = get_object_or_404(SQBPackage, pk=pk)
    if request.method != "POST":
        return redirect("sqb:package_detail", pk=pk)

    if not package.all_parts_uploaded():
        messages.error(request, "Every part needs a file uploaded before closing.")
        return redirect("sqb:package_detail", pk=pk)

    try:
        from pypdf import PdfWriter
    except ImportError:
        messages.error(request, "pypdf is not installed on the server \u2014 run: pip install pypdf --break-system-packages")
        return redirect("sqb:package_detail", pk=pk)

    writer = PdfWriter()
    for part in package.parts.select_related("part_definition").order_by("part_definition__order"):
        if part.file:
            with default_storage.open(part.file.name, "rb") as f:
                writer.append(f)

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)

    filename = f"SQB_{package.job_name}_{package.sqb_type.name}.pdf".replace(" ", "_")
    package.combined_file.save(filename, ContentFile(buffer.read()), save=False)
    package.status = "Closed"
    package.closed_date = timezone.localdate()
    package.closed_by = request.user if request.user.is_authenticated else None
    package.save()

    messages.success(request, "SQB closed and combined PDF generated.")
    return redirect("sqb:package_detail", pk=pk)


def type_manage(request):
    types = SQBType.objects.all()
    if request.method == "POST":
        form = SQBTypeForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("sqb:type_manage")
    else:
        form = SQBTypeForm()
    return render(request, "sqb/type_manage.html", {"types": types, "form": form})


def type_delete(request, pk):
    t = get_object_or_404(SQBType, pk=pk)
    if request.method == "POST":
        try:
            t.delete()
            messages.success(request, f"Type '{t.name}' deleted.")
        except ProtectedError:
            messages.error(request, f"Can't delete '{t.name}' \u2014 packages still use this type.")
    return redirect("sqb:type_manage")


def type_detail(request, pk):
    sqb_type = get_object_or_404(SQBType, pk=pk)
    if request.method == "POST":
        form = SQBPartDefinitionForm(request.POST)
        if form.is_valid():
            part = form.save(commit=False)
            part.sqb_type = sqb_type
            part.save()
            return redirect("sqb:type_detail", pk=pk)
    else:
        form = SQBPartDefinitionForm()
    return render(request, "sqb/type_detail.html", {
        "sqb_type": sqb_type, "parts": sqb_type.parts.all(), "form": form,
    })


def part_definition_delete(request, pk):
    part = get_object_or_404(SQBPartDefinition, pk=pk)
    type_pk = part.sqb_type_id
    if request.method == "POST":
        part.delete()
    return redirect("sqb:type_detail", pk=type_pk)


# ── Legacy SQBJob/SQBDocument (kept working, no longer the main entry point) ──

def legacy_job_list(request):
    jobs = SQBJob.objects.all()
    q = request.GET.get("q")
    if q:
        jobs = jobs.filter(job_number__icontains=q) | jobs.filter(job_name__icontains=q)
    return render(request, "sqb/job_list.html", {"jobs": jobs, "q": q or ""})


def job_create(request):
    if request.method == "POST":
        form = SQBJobForm(request.POST)
        if form.is_valid():
            job = form.save()
            messages.success(request, "Job created.")
            return redirect("sqb:legacy_job_detail", pk=job.pk)
    else:
        form = SQBJobForm()
    return render(request, "sqb/job_form.html", {"form": form})


def job_edit(request, pk):
    job = get_object_or_404(SQBJob, pk=pk)
    if request.method == "POST":
        form = SQBJobForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            return redirect("sqb:legacy_job_detail", pk=pk)
    else:
        form = SQBJobForm(instance=job)
    return render(request, "sqb/job_form.html", {"form": form, "job": job})


def job_delete(request, pk):
    job = get_object_or_404(SQBJob, pk=pk)
    if request.method == "POST":
        job.delete()
        messages.success(request, "Deleted.")
        return redirect("sqb:legacy_job_list")
    return render(request, "sqb/job_confirm_delete.html", {"job": job})


def legacy_job_detail(request, pk):
    job = get_object_or_404(SQBJob, pk=pk)
    if request.method == "POST":
        form = SQBDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.job = job
            doc.save()
            return redirect("sqb:legacy_job_detail", pk=pk)
    else:
        form = SQBDocumentForm()
    return render(request, "sqb/job_detail.html", {
        "job": job, "documents": job.documents.all(), "form": form,
    })


@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(SQBDocument, pk=pk)
    job_pk = doc.job_id
    doc.delete()
    return redirect("sqb:legacy_job_detail", pk=job_pk)


def _npt_context(request):
    """Everything the NPT pages share: period, entries, client setups, jobs, KPIs, colours."""
    from job_reports.models import JobReport
    from wire_management.models import Client

    from . import npt
    from .models import ClientNPTSetting, NPTKPI

    period = npt.parse_period(request.GET)
    everything = list(SQBTrackerEntry.objects.select_related("unit__classification__department", "job_report__manifest"))
    settings = list(ClientNPTSetting.objects.prefetch_related("units"))
    jobs = list(JobReport.objects.filter(callout_start__isnull=False, callout_end__isnull=False).select_related("manifest"))
    kpis = list(NPTKPI.objects.filter(is_active=True).select_related("unit"))
    client_names = sorted({c for c in Client.objects.values_list("name", flat=True)}
                          | {e.client for e in everything if e.client} | {s.client for s in settings})
    return {
        "npt": npt, "period": period, "everything": everything, "settings": settings, "jobs": jobs, "kpis": kpis,
        "client_names": client_names, "colours": npt.colour_map(client_names),
        "dated": npt.in_range(everything, period["start"], period["end"]),
    }


def _metric(request, settings, kpis=(), client="", unit_id="", category=""):
    """'percent' or 'hours': what the chart and KPI line are drawn in.

    Unless the user chose, prefer the one that has a KPI line to draw for this view."""
    asked = request.GET.get("show")
    if asked in ("hours", "percent"):
        return asked
    from . import npt
    first = "percent" if settings else "hours"
    other = "hours" if first == "percent" else "percent"
    def usable(metric):
        return npt.pick_chart_kpi(kpis, metric, client, unit_id, category) is not None
    def exists(metric):
        return any(k.metric == metric and (not client or not k.client or k.client == client) for k in kpis)
    if not usable(first) and (usable(other) or (not exists(first) and exists(other))):
        return other
    return first


def tracker_list(request):
    """NPT dashboard: entries, KPI scorecard and charts for a chosen period."""
    if request.method == "POST":
        form = SQBTrackerEntryForm(request.POST)
        if form.is_valid():
            entry = form.save()
            messages.success(request, f"NPT logged: {entry.npt_hours:g} h on {entry.unit} ({entry.job_report.name}).")
            return redirect("sqb:tracker_list")
    else:
        form = SQBTrackerEntryForm()

    ctx = _npt_context(request)
    npt, period, settings, jobs, kpis, dated = ctx["npt"], ctx["period"], ctx["settings"], ctx["jobs"], ctx["kpis"], ctx["dated"]
    client = request.GET.get("client", "")
    unit_id = request.GET.get("unit", "")
    category = request.GET.get("category", "")
    metric = _metric(request, settings, kpis, client, unit_id, category)
    unit_ids = [int(unit_id)] if unit_id.isdigit() else None

    shown = npt.apply_filters(dated, client, unit_id, category)
    scorecard = npt.evaluate_kpis(kpis, dated, period["start"], period["end"], settings=settings, jobs=jobs)
    chart_kpi = npt.pick_chart_kpi(kpis, metric, client, unit_id, category)
    line = float(chart_kpi.target) if chart_kpi else None
    series = npt.time_series(shown, period["start"], period["end"], kpi_line=line, metric=metric, settings=settings,
                             jobs=jobs, client=client, unit_ids=unit_ids, all_entries=dated)

    causes = npt.breakdown(shown, key=lambda e: e.category, label=lambda e: npt.CATEGORY_LABELS.get(e.category, "Not classified"))

    # The client and unit tables ignore their own filter so they can still be compared with each other.
    clients_table = npt.client_table(ctx["client_names"], npt.apply_filters(dated, "", unit_id, category),
                                     settings, jobs, period["start"], period["end"], colours=ctx["colours"])
    clients_table = [c for c in clients_table if c["count"] or c["setting"]]
    all_units = list(unit_queryset())
    scoped_settings = [s for s in settings if not client or s.client == client]
    units = npt.unit_table(all_units, npt.apply_filters(dated, client, "", category), scoped_settings, jobs,
                           period["start"], period["end"])
    unit_kpis = {}
    for row in scorecard:
        if row["kpi"].unit_id and not row["kpi"].client and not row["kpi"].category:
            unit_kpis.setdefault(row["kpi"].unit_id, row)
    for u in units:
        u["kpi"] = unit_kpis.get(u["key"])
        u["selected"] = bool(unit_id) and str(u["key"]) == unit_id
    multi_department = len({u["department"] for u in units if u["department"]}) > 1

    total = round(sum(npt.hours(e) for e in shown), 1)
    today = datetime.date.today()
    months = max(1, len([m for m in npt.months_between(period["start"], period["end"]) if m <= today]))
    available, has_setup = npt.available_for(settings, period["start"], period["end"], jobs, dated, client, unit_ids)
    overall_percent = npt.percent_of(total, available) if has_setup else None
    known = [r for r in scorecard if r["status"] != "unknown"]
    within = sum(1 for r in known if r["status"] != "over")
    selected_setting = next((s for s in settings if s.client == client), None) if client else None

    return render(request, "sqb/tracker_list.html", {
        "form": form, "period": period, "period_choices": npt.PERIOD_CHOICES,
        "client": client, "unit_id": unit_id, "category": category, "metric": metric, "has_setup": bool(settings),
        "entries": sorted(shown, key=lambda e: (e.date, e.pk), reverse=True),
        "total": total, "entry_count": len(shown), "monthly_average": round(total / months, 1),
        "available": round(available, 1), "overall_percent": overall_percent,
        "top_cause": causes[0] if causes else None,
        "scorecard": scorecard, "kpis_within": within, "kpis_known": len(known),
        "chart_kpi": chart_kpi,
        "scoped_kpis": ", ".join(k.name for k in kpis if k.metric == metric and (not client or not k.client or k.client == client)),
        "series": series, "causes": causes, "units": units, "clients": clients_table,
        "colours": ctx["colours"], "selected_setting": selected_setting,
        "filter_clients": ctx["client_names"], "filter_units": all_units, "multi_department": multi_department,
        "category_choices": NPT_CATEGORY_CHOICES,
        "filters_active": bool(client or unit_id or category),
    })


def tracker_compare(request):
    """Two or more units of one client, side by side."""
    ctx = _npt_context(request)
    npt, period, settings, jobs, kpis, dated = ctx["npt"], ctx["period"], ctx["settings"], ctx["jobs"], ctx["kpis"], ctx["dated"]
    client = request.GET.get("client", "")
    metric = _metric(request, settings, kpis, client)
    setting = next((s for s in settings if s.client == client), None)

    everything_units = list(unit_queryset())
    # A client's own units when they are set up; otherwise any unit can be compared.
    choices = list(setting.units.all()) if setting and setting.units.exists() else everything_units
    chosen_ids = [int(u) for u in request.GET.getlist("units") if u.isdigit()]
    chosen = [u for u in choices if u.pk in chosen_ids] or (choices if client and not chosen_ids else [])
    client_entries = [e for e in dated if e.client == client]

    # Which KPI to draw: any KPI of this client (or all clients) in the chosen metric; "none" hides the line.
    kpi_choices = [k for k in kpis if k.metric == metric and (not k.client or k.client == client)]
    asked = request.GET.get("kpi", "")
    kpi = None
    if asked != "none":
        kpi = next((k for k in kpi_choices if str(k.pk) == asked), None) or npt.pick_chart_kpi(kpis, metric, client)
        if kpi is None and kpi_choices:
            on_chosen = [k for k in kpi_choices if k.unit_id in {u.pk for u in chosen}] or kpi_choices
            kpi = on_chosen[0]

    comparison = chart = None
    if client and len(chosen) >= 1:
        comparison = npt.compare_units(chosen, client_entries, setting, jobs, period["start"], period["end"], metric,
                                       monthly=bool(kpi and metric == "hours"))
        chart = npt.compare_chart(comparison, float(kpi.target) if kpi else None, metric)
        chart["kpi"] = kpi
    return render(request, "sqb/tracker_compare.html", {
        "period": period, "period_choices": npt.PERIOD_CHOICES, "client": client, "metric": metric,
        "setting": setting, "unit_choices": choices, "chosen_ids": {u.pk for u in chosen},
        "comparison": comparison, "chart": chart, "kpi_choices": kpi_choices, "kpi_asked": asked,
        "kpi_selected": kpi, "filter_clients": ctx["client_names"],
        "colours": ctx["colours"], "few_units": comparison is not None and len(chosen) < 2,
    })


def client_npt_manage(request):
    """Every client with its NPT basis (or a prompt to set one up)."""
    from wire_management.models import Client

    from .models import ClientNPTSetting

    settings = {s.client: s for s in ClientNPTSetting.objects.prefetch_related("units")}
    rows = [{"client": c, "setting": settings.get(c.name)} for c in Client.objects.all()]
    return render(request, "sqb/client_npt_manage.html", {"rows": rows})


def client_npt_edit(request, client_pk):
    from wire_management.models import Client

    from .models import ClientNPTSetting

    client = get_object_or_404(Client, pk=client_pk)
    setting, _ = ClientNPTSetting.objects.get_or_create(client=client.name)
    form = ClientNPTSettingForm(request.POST or None, instance=setting)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{client.name}: NPT will be worked out on \u201c{setting.basis_text}\u201d.")
        return redirect("sqb:client_npt_manage")
    return render(request, "sqb/client_npt_edit.html", {"form": form, "client": client, "setting": setting})


def tracker_edit(request, pk):
    entry = get_object_or_404(SQBTrackerEntry, pk=pk)
    form = SQBTrackerEntryForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "NPT entry updated.")
        return redirect("sqb:tracker_list")
    return render(request, "sqb/tracker_entry_form.html", {"form": form, "entry": entry})


def kpi_manage(request):
    from .models import NPTKPI

    form = NPTKPIForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "KPI added.")
        return redirect("sqb:kpi_manage")
    return render(request, "sqb/kpi_manage.html", {
        "form": form, "kpis": NPTKPI.objects.select_related("unit"),
    })


def kpi_edit(request, pk):
    from .models import NPTKPI

    kpi = get_object_or_404(NPTKPI, pk=pk)
    form = NPTKPIForm(request.POST or None, instance=kpi)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "KPI updated.")
        return redirect("sqb:kpi_manage")
    return render(request, "sqb/kpi_edit.html", {"form": form, "kpi": kpi})


@require_POST
def kpi_delete(request, pk):
    from .models import NPTKPI

    get_object_or_404(NPTKPI, pk=pk).delete()
    messages.success(request, "KPI deleted.")
    return redirect("sqb:kpi_manage")


def tracker_delete(request, pk):
    entry = get_object_or_404(SQBTrackerEntry, pk=pk)
    if request.method == "POST":
        entry.delete()
    return redirect("sqb:tracker_list")