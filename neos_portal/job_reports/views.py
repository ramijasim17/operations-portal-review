import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .forms import JobReportDocumentForm, JobReportForm, JobSupervisorForm
from .models import JobReport, JobReportDocument, JobSupervisor


def _create_linked_sgs_report(job_report):
    """Auto-creates the matching SGSReport when a Job Report is flagged
    as needing an SGS/FGS/PBU report. sgs_report's own report_type field
    only ever stores "PBU" or "SGS" -- FGS is expressed via the
    free-text test_type field instead (e.g. "Formation Gradient
    Survey"), matching how sgs_report/models.py actually defines
    REPORT_TYPE_CHOICES."""
    from sgs_report.models import SGSReport

    survey_type = job_report.survey_report_type
    if survey_type == "PBU":
        report_type, test_type = "PBU", "PBU-Static Gradient Survey"
    elif survey_type == "FGS":
        report_type, test_type = "SGS", "Formation Gradient Survey"
    else:  # "SGS"
        report_type, test_type = "SGS", "Static Gradient Survey"

    manifest = job_report.manifest
    report = SGSReport.objects.create(
        well_name=job_report.name,
        client=manifest.client,
        date_of_test=manifest.date,
        report_type=report_type,
        test_type=test_type,
    )
    job_report.linked_sgs_report = report
    job_report.save(update_fields=["linked_sgs_report"])


def _sqb_state(report):
    """Missing / Open / Closed, from the SQB packages linked to this job."""
    packages = list(report.sqb_packages.all())
    if not packages:
        return "Missing"
    return "Open" if any(p.status == "Open" for p in packages) else "Closed"


def job_report_list(request):
    from django.db.models import Count, Q

    q = request.GET.get("q", "").strip()
    reports = (
        JobReport.objects.select_related("manifest", "linked_sgs_report")
        .prefetch_related("sqb_packages", "assignments__person")
        .annotate(doc_count=Count("documents", distinct=True))
    )
    if q:
        reports = reports.filter(Q(name__icontains=q) | Q(manifest__job_number__icontains=q)
                                 | Q(manifest__job_name__icontains=q) | Q(manifest__client__icontains=q)
                                 | Q(assignments__person__name__icontains=q))
    rows = list(reports.distinct())
    for r in rows:
        r.sqb_state = _sqb_state(r)
    stats = {
        "total": len(rows),
        "survey": sum(1 for r in rows if r.needs_survey_report),
        "sqb_missing": sum(1 for r in rows if r.sqb_state == "Missing"),
        "documents": sum(r.doc_count for r in rows),
    }
    return render(request, "job_reports/job_report_list.html", {"reports": rows, "q": q, "stats": stats})


def job_report_create(request):
    if request.method == "POST":
        form = JobReportForm(request.POST)
        if form.is_valid():
            report = form.save()
            messages.success(request, f"Job '{report.name}' created \u2014 it is now listed under SQB \u2192 Missing SQB.")
            if report.needs_survey_report and report.survey_report_type and not report.linked_sgs_report_id:
                _create_linked_sgs_report(report)
                messages.success(request, f"A {report.survey_report_type} report was created in SGS Reports for this job.")
            return redirect("job_reports:detail", pk=report.pk)
    else:
        form = JobReportForm()
    return render(request, "job_reports/job_report_form.html", {"form": form})


def job_report_edit(request, pk):
    report = get_object_or_404(JobReport, pk=pk)
    if request.method == "POST":
        form = JobReportForm(request.POST, instance=report)
        if form.is_valid():
            report = form.save()
            if report.needs_survey_report and report.survey_report_type and not report.linked_sgs_report_id:
                _create_linked_sgs_report(report)
                messages.success(request, f"A {report.survey_report_type} report was created in SGS Reports for this job.")
            return redirect("job_reports:detail", pk=pk)
    else:
        form = JobReportForm(instance=report)
    return render(request, "job_reports/job_report_form.html", {"form": form, "report": report})


def job_report_delete(request, pk):
    report = get_object_or_404(JobReport, pk=pk)
    if request.method == "POST":
        report.delete()
        messages.success(request, "Job report deleted.")
        return redirect("job_reports:list")
    return render(request, "job_reports/job_report_confirm_delete.html", {"report": report})


def job_report_detail(request, pk):
    report = get_object_or_404(JobReport, pk=pk)
    if request.method == "POST":
        doc_form = JobReportDocumentForm(request.POST, request.FILES)
        if doc_form.is_valid():
            doc = doc_form.save(commit=False)
            doc.job_report = report
            doc.save()
            messages.success(request, "Document uploaded.")
            return redirect("job_reports:detail", pk=pk)
    else:
        doc_form = JobReportDocumentForm()
    documents = report.documents.all()
    return render(request, "job_reports/job_report_detail.html", {
        "report": report, "assignments": list(report.assignments.select_related("person")),
        "today": datetime.date.today(), "doc_form": doc_form, "documents": documents,
        "sqb_state": _sqb_state(report),
    })


def job_report_document_delete(request, pk):
    doc = get_object_or_404(JobReportDocument, pk=pk)
    report_pk = doc.job_report_id
    if request.method == "POST":
        doc.delete()
        messages.success(request, "Document deleted.")
    return redirect("job_reports:detail", pk=report_pk)


def supervisor_add(request, pk):
    report = get_object_or_404(JobReport, pk=pk)
    form = JobSupervisorForm(request.POST or None, job=report)
    if request.method == "POST" and form.is_valid():
        a = form.save()
        messages.success(request, f"{a.person.name} added as {a.get_shift_display().lower()} supervisor.")
        return redirect("job_reports:detail", pk=pk)
    return render(request, "job_reports/supervisor_form.html", {"form": form, "report": report})


def supervisor_edit(request, pk):
    a = get_object_or_404(JobSupervisor.objects.select_related("job_report", "person"), pk=pk)
    form = JobSupervisorForm(request.POST or None, instance=a)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Supervisor updated.")
        return redirect("job_reports:detail", pk=a.job_report_id)
    return render(request, "job_reports/supervisor_form.html", {"form": form, "report": a.job_report, "assignment": a})


def supervisor_delete(request, pk):
    a = get_object_or_404(JobSupervisor, pk=pk)
    job_pk = a.job_report_id
    if request.method == "POST":
        a.delete()
        messages.success(request, "Supervisor removed from the job.")
    return redirect("job_reports:detail", pk=job_pk)
