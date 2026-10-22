from django.contrib import messages
from django.views.decorators.http import require_POST
from django.utils.http import content_disposition_header
from django.shortcuts import get_object_or_404, redirect, render

from core.dept_scope import get_scope, is_restricted, personnel_filter

from .forms import PersonnelCertForm, PersonnelForm, TrainingCourseForm
from .models import Personnel, PersonnelCert, TrainingCourse


def _people(request):
    """The people this person may see: their own department's (Level 1 / 2), or the department chosen (Level 3 / Admin)."""
    return personnel_filter(Personnel.objects.all(), get_scope(request))


def _person_or_404(request, pk):
    qs = _people(request) if is_restricted(request.user) else Personnel.objects.all()
    return get_object_or_404(qs, pk=pk)


def personnel_list(request):
    """The Personnel dashboard — three tabs: Employees, Required Courses,
    Certificates. All three used to be scattered across separate pages;
    this consolidates them under one nav entry with a tab switcher."""
    import datetime

    from django.db.models import Count, Q

    tab = request.GET.get("tab", "employees")
    today = datetime.date.today()
    soon = today + datetime.timedelta(days=30)

    current = Q(job_assignments__start_date__lte=today) & (Q(job_assignments__end_date__isnull=True) | Q(job_assignments__end_date__gte=today))
    scope = get_scope(request)
    base = _people(request)
    people = base.annotate(
        cert_total=Count("certs", distinct=True),
        cert_expired=Count("certs", filter=Q(certs__expiry_date__lt=today), distinct=True),
        cert_soon=Count("certs", filter=Q(certs__expiry_date__gte=today, certs__expiry_date__lte=soon), distinct=True),
        job_count=Count("job_assignments__job_report", distinct=True),
        on_job=Count("job_assignments", filter=current, distinct=True),
    ).prefetch_related("approvals")
    q = request.GET.get("q")
    status = request.GET.get("status")
    department = request.GET.get("department", "")
    position = request.GET.get("position", "")
    cert_state = request.GET.get("certs", "")
    view = "table" if request.GET.get("view") == "table" else "cards"
    sort = request.GET.get("sort", "name")
    if q:
        people = people.filter(Q(name__icontains=q) | Q(employee_id__icontains=q) | Q(position__icontains=q))
    if status:
        people = people.filter(status=status)
    if department:
        people = people.filter(department=department)
    if position:
        people = people.filter(position=position)
    if cert_state == "expired":
        people = people.filter(cert_expired__gt=0)
    elif cert_state == "soon":
        people = people.filter(cert_soon__gt=0)
    elif cert_state == "none":
        people = people.filter(cert_total=0)
    elif cert_state == "ok":
        people = people.filter(cert_total__gt=0, cert_expired=0, cert_soon=0)
    people = people.order_by("-cert_expired", "-cert_soon", "name") if sort == "attention" else people.order_by("name")
    people = list(people)
    for p in people:
        p.cert_ok = max(p.cert_total - p.cert_expired - p.cert_soon, 0)
        total = p.cert_total or 1
        p.w_ok, p.w_soon, p.w_expired = (round(100 * n / total) for n in (p.cert_ok, p.cert_soon, p.cert_expired))

    active_certs = PersonnelCert.objects.filter(personnel__status="Active", personnel__in=base)
    stats = {
        "active": base.filter(status="Active").count(),
        "inactive": base.filter(status="Inactive").count(),
        "expired": active_certs.filter(expiry_date__lt=today).count(),
        "soon": active_certs.filter(expiry_date__gte=today, expiry_date__lte=soon).count(),
    }

    context = {
        "people": people, "q": q or "", "status": status or "", "tab": tab, "stats": stats,
        "department": department, "position": position, "cert_state": cert_state, "view": view, "sort": sort,
        "departments": sorted(set(base.exclude(department="").values_list("department", flat=True))),
        "positions": sorted(set(base.exclude(position="").values_list("position", flat=True))),
        "scope": scope,
        "filtered": bool(q or status or department or position or cert_state),
    }

    if tab == "courses":
        all_courses = TrainingCourse.objects.all()
        active_people = list(base.filter(status="Active").prefetch_related("required_training_courses"))
        required_map = {p.id: {c.id for c in p.required_training_courses.all()} for p in active_people}
        context.update({"active_people": active_people, "all_courses": all_courses, "required_map": required_map})

    elif tab == "certificates":
        view_mode = "cards" if request.GET.get("view") == "cards" else "matrix"
        show = request.GET.get("show", "")
        sort = request.GET.get("sort", "attention")
        people_qs = base.filter(status="Active").prefetch_related("required_training_courses", "certs")
        if q:
            people_qs = people_qs.filter(Q(name__icontains=q) | Q(employee_id__icontains=q))
        courses = list(TrainingCourse.objects.all())
        rows, totals = [], {c.pk: {"missing": 0, "expired": 0, "soon": 0, "valid": 0} for c in courses}
        for p in people_qs:
            required = {c.pk for c in p.required_training_courses.all()}
            by_course = {c.course_id: c for c in p.certs.all()}
            cells, counts = [], {"missing": 0, "expired": 0, "soon": 0, "valid": 0}
            for course in courses:
                cert = by_course.get(course.pk)
                if course.pk not in required and cert is None:
                    cells.append({"course": course, "kind": "na", "cert": None})
                    continue
                if cert is None:
                    kind = "missing"
                elif not cert.expiry_date or cert.expiry_date > soon:
                    kind = "valid"
                elif cert.expiry_date < today:
                    kind = "expired"
                else:
                    kind = "soon"
                counts[kind] += 1
                totals[course.pk][kind] += 1
                cells.append({"course": course, "kind": kind, "cert": cert, "required": course.pk in required,
                              "days": (cert.expiry_date - today).days if cert and cert.expiry_date else None})
            p.cells, p.cert_counts = cells, counts
            p.issues = counts["missing"] + counts["expired"] + counts["soon"]
            if show == "attention" and not p.issues:
                continue
            if show in ("missing", "expired", "soon") and not counts[show]:
                continue
            rows.append(p)
        if sort == "attention":
            rows.sort(key=lambda p: (-p.cert_counts["expired"], -p.cert_counts["missing"], -p.cert_counts["soon"], p.name.lower()))
        else:
            rows.sort(key=lambda p: p.name.lower())
        certs_by_person = {p.id: {c.course_id: c for c in p.certs.all()} for p in rows}
        summary = {k: sum(p.cert_counts[k] for p in rows) for k in ("missing", "expired", "soon", "valid")}
        context.update({
            "active_people": rows, "courses": courses, "certs_by_person": certs_by_person,
            "view_mode": view_mode, "show": show, "sort": sort, "summary": summary,
            "course_totals": [(c, totals[c.pk]) for c in courses], "cert_filtered": bool(q or show),
        })

    return render(request, "personnel/personnel_list.html", context)


def personnel_detail(request, pk):
    """One person: their details, certificates and every job they supervised."""
    import datetime

    person = _person_or_404(request, pk)
    today = datetime.date.today()
    assignments = list(person.job_assignments.select_related("job_report__manifest"))
    jobs = {}
    for a in assignments:
        row = jobs.setdefault(a.job_report_id, {"job": a.job_report, "stints": [], "days": 0, "current": False})
        row["stints"].append(a)
        row["days"] += ((a.end_date or today) - a.start_date).days + 1 if a.start_date <= today else 0
        row["current"] = row["current"] or a.is_current(today)
    rows = sorted(jobs.values(), key=lambda r: (not r["current"], -max(a.start_date.toordinal() for a in r["stints"])))
    certs = list(person.certs.select_related("course").order_by("expiry_date"))
    return render(request, "personnel/personnel_detail.html", {
        "person": person, "jobs": rows, "today": today, "certs": certs,
        "current_count": sum(r["current"] for r in rows), "total_days": sum(r["days"] for r in rows),
        "by_shift": {key: sum(1 for a in assignments if a.shift == key) for key in ("day", "night", "both")},
    })


def required_courses_save(request):
    """Saves the whole Required Courses matrix in one submit — checkbox
    name is required_<personnel_id>_<course_id>."""
    if request.method == "POST":
        for person in _people(request).filter(status="Active"):
            checked_ids = []
            for course in TrainingCourse.objects.all():
                if request.POST.get(f"required_{person.id}_{course.id}"):
                    checked_ids.append(course.id)
            person.required_training_courses.set(checked_ids)
        messages.success(request, "Required courses updated.")
    return redirect("/personnel/?tab=courses")


def personnel_create(request):
    scope = get_scope(request)
    if scope.mode == "blocked":
        messages.error(request, "Your account is not linked to a department yet, so you can't add people. Ask an admin to set your department.")
        return redirect("personnel:list")
    if request.method == "POST":
        form = PersonnelForm(request.POST, scope=scope)
        if form.is_valid():
            form.save()
            messages.success(request, "Personnel record created.")
            return redirect("personnel:list")
    else:
        form = PersonnelForm(scope=scope, initial={"department": scope.name} if scope.mode == "dept" else None)
    return render(request, "personnel/personnel_form.html", {"form": form})


def personnel_edit(request, pk):
    person = _person_or_404(request, pk)
    scope = get_scope(request)
    if request.method == "POST":
        form = PersonnelForm(request.POST, instance=person, scope=scope)
        if form.is_valid():
            form.save()
            return redirect("personnel:list")
    else:
        form = PersonnelForm(instance=person, scope=scope)
    return render(request, "personnel/personnel_form.html", {"form": form, "person": person})


def personnel_delete(request, pk):
    person = _person_or_404(request, pk)
    if request.method == "POST":
        person.delete()
        messages.success(request, "Deleted.")
    return redirect("personnel:list")


@require_POST
def personnel_request_removal(request, pk):
    """Level 2 can't delete a person; it asks, and a Level 3 approves (which deletes them)."""
    from approvals import services as approvals

    person = _person_or_404(request, pk)
    if request.user.profile.can_delete:
        person.delete()
        messages.success(request, "Removed.")
    else:
        approvals.submit(person, "personnel_removal", request.user)
        messages.success(request, f"Removal of {person.name} sent to a Level 3 for approval. They stay until then.")
    return redirect("personnel:list")


def cert_matrix(request):
    people = _people(request).filter(status="Active")
    courses = TrainingCourse.objects.all()
    certs_by_person = {}
    for p in people:
        certs_by_person[p.id] = {c.course_id: c for c in p.certs.all()}
    return render(request, "personnel/cert_matrix.html", {
        "people": people, "courses": courses, "certs_by_person": certs_by_person,
    })


def cert_create(request):
    people = _people(request)
    if request.method == "POST":
        form = PersonnelCertForm(request.POST, request.FILES, people=people)
        if form.is_valid():
            form.save()
            messages.success(request, "Certificate added.")
            return redirect("/personnel/?tab=certificates")
    else:
        form = PersonnelCertForm(initial={
            "personnel": request.GET.get("personnel"),
            "course": request.GET.get("course"),
        }, people=people)
    return render(request, "personnel/cert_form.html", {"form": form})


def cert_edit(request, pk):
    cert = get_object_or_404(PersonnelCert, pk=pk)
    _person_or_404(request, cert.personnel_id)
    people = _people(request)
    if request.method == "POST":
        form = PersonnelCertForm(request.POST, request.FILES, instance=cert, people=people)
        if form.is_valid():
            form.save()
            return redirect("/personnel/?tab=certificates")
    else:
        form = PersonnelCertForm(instance=cert, people=people)
    return render(request, "personnel/cert_form.html", {"form": form, "cert": cert})


def course_manage(request):
    courses = TrainingCourse.objects.all()
    if request.method == "POST":
        form = TrainingCourseForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("personnel:course_manage")
    else:
        form = TrainingCourseForm()
    return render(request, "personnel/course_manage.html", {"courses": courses, "form": form})


def course_delete(request, pk):
    course = get_object_or_404(TrainingCourse, pk=pk)
    if request.method == "POST":
        course.delete()
    return redirect("personnel:course_manage")


def course_certs_zip_export(request, course_id):
    """Bundles every uploaded certificate file for one course, across
    all employees, into a single ZIP -- named by employee name + ID so
    two people sharing a name (or an identically-named uploaded file)
    never collide or overwrite each other."""
    import io
    import zipfile

    from django.http import HttpResponse

    course = get_object_or_404(TrainingCourse, pk=course_id)
    certs = PersonnelCert.objects.filter(course=course, personnel__in=_people(request)).exclude(file="").select_related("personnel")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if certs.exists():
            for cert in certs:
                ext = cert.file.name.rsplit(".", 1)[-1] if "." in cert.file.name else "pdf"
                safe_name = f"{cert.personnel.name}_{cert.personnel.employee_id}.{ext}".replace("/", "-")
                with cert.file.open("rb") as f:
                    zf.writestr(safe_name, f.read())
        else:
            zf.writestr("README.txt", f"No certificates with uploaded files were found for '{course.course_name}'.")

    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type="application/zip")
    response["Content-Disposition"] = content_disposition_header(True, f"{course.course_name}_certificates.zip")
    return response


def _parse_personnel_cert_filename(filename):
    """
    First underscore-separated part is always the employee ID, last part
    holds the expiry date -- opposite order from the equipment cert
    uploader, matching "employee-id_Name_ddmmyyyy". Anything in between
    (name) is read but ignored for matching. The date may also be written
    yyyymmdd behind an "EXP" marker (core/cert_filenames.py).
    """
    import os

    from core.cert_filenames import drop_marker_parts, read_date

    name_only = os.path.splitext(filename)[0]
    parts = name_only.split("_")
    if len(parts) < 2:
        return None, None, "Filename needs at least an employee ID and a date separated by _."

    employee_id = parts[0].strip()
    rest = drop_marker_parts(parts[1:])
    date_text = rest[-1].strip() if rest else ""
    expiry_date = read_date(date_text) if date_text else None
    if expiry_date is None:
        return None, None, f"Couldn't read a date from '{date_text}' (the last part of the filename should be ddmmyyyy, or EXP followed by yyyymmdd)."

    return expiry_date, employee_id, None


def personnel_bulk_cert_upload(request):
    import uuid

    from django.core.files.storage import default_storage

    if request.method == "POST":
        course_id = request.POST.get("course")
        files = request.FILES.getlist("files")
        course = TrainingCourse.objects.filter(pk=course_id).first()

        if not course:
            messages.error(request, "Choose a course first.")
            return redirect("personnel:bulk_cert_upload")
        if not files:
            messages.error(request, "Choose at least one file to upload.")
            return redirect("personnel:bulk_cert_upload")

        batch_id = uuid.uuid4().hex
        manifest = []
        for f in files:
            expiry_date, employee_id, error = _parse_personnel_cert_filename(f.name)
            entry = {"file": f.name, "expiry_date": None, "employee_id": employee_id, "error": error, "personnel_id": None}

            if error:
                manifest.append(entry)
                continue

            person = _people(request).filter(employee_id=employee_id).first()
            if not person:
                entry["error"] = f"No employee with ID '{employee_id}' found."
                manifest.append(entry)
                continue

            temp_path = default_storage.save(f"tmp_bulk_personnel_certs/{batch_id}/{f.name}", f)
            entry["expiry_date"] = expiry_date.isoformat()
            entry["personnel_id"] = person.id
            entry["personnel_name"] = person.name
            entry["temp_path"] = temp_path
            manifest.append(entry)

        request.session[f"bulk_pcert_batch_{batch_id}"] = {
            "course_id": course.id, "course_name": course.course_name, "manifest": manifest,
        }
        return redirect("personnel:bulk_cert_preview", batch_id=batch_id)

    return render(request, "personnel/bulk_cert_upload.html", {"courses": TrainingCourse.objects.all()})


def personnel_bulk_cert_preview(request, batch_id):
    session_key = f"bulk_pcert_batch_{batch_id}"
    batch = request.session.get(session_key)
    if not batch:
        messages.error(request, "That upload batch has expired or was already processed. Please upload again.")
        return redirect("personnel:bulk_cert_upload")

    matched = [m for m in batch["manifest"] if not m["error"]]
    unmatched = [m for m in batch["manifest"] if m["error"]]

    return render(request, "personnel/bulk_cert_preview.html", {
        "batch_id": batch_id, "course_name": batch["course_name"],
        "matched": matched, "unmatched": unmatched,
    })


def personnel_bulk_cert_confirm(request, batch_id):
    import datetime

    from django.core.files import File
    from django.core.files.storage import default_storage

    session_key = f"bulk_pcert_batch_{batch_id}"
    batch = request.session.get(session_key)
    if not batch or request.method != "POST":
        messages.error(request, "Nothing to confirm — that batch has expired.")
        return redirect("personnel:bulk_cert_upload")

    course = TrainingCourse.objects.filter(pk=batch["course_id"]).first()
    results = []

    for m in batch["manifest"]:
        if m["error"]:
            results.append({"file": m["file"], "ok": False, "reason": m["error"]})
            continue

        person = _people(request).filter(pk=m["personnel_id"]).first()
        if not person or not course:
            results.append({"file": m["file"], "ok": False, "reason": "Employee or course no longer exists."})
            continue

        expiry_date = datetime.date.fromisoformat(m["expiry_date"])

        with default_storage.open(m["temp_path"]) as temp_file:
            django_file = File(temp_file, name=m["file"])
            cert, created = PersonnelCert.objects.update_or_create(
                personnel=person, course=course,
                defaults={"expiry_date": expiry_date, "file": django_file, "notes": ""},
            )
        default_storage.delete(m["temp_path"])

        results.append({
            "file": m["file"], "ok": True,
            "reason": f"{'Created' if created else 'Updated'} cert for {person.name} — {course.course_name}, expires {expiry_date}.",
        })

    del request.session[session_key]
    ok_count = sum(1 for r in results if r["ok"])
    messages.success(request, f"Saved {ok_count} of {len(results)} certificate(s).")

    return render(request, "personnel/bulk_cert_upload.html", {
        "results": results, "courses": TrainingCourse.objects.all(),
    })


def csv_import(request):
    """
    Imports a wide-format CSV: Name, ID, Dept, Position, Status, then one
    column per training course. Each cell is either a date (expiry),
    "Missing" (course needed but not held — no record created, so it
    shows as needing attention), or "N/A" (not required for this role —
    a record is still created so it displays as "na" instead of looking
    like a gap). Matches existing people by name; creates new ones with a
    generated employee ID if no match is found (flagged in the results).
    """
    import csv
    import datetime
    import io

    results = {"updated": 0, "created": 0, "certs": 0, "flagged": []}
    scope = get_scope(request)
    if scope.mode == "blocked":
        messages.error(request, "Your account is not linked to a department yet. Ask an admin to set your department.")
        return redirect("personnel:list")

    if request.method == "POST" and request.FILES.get("file"):
        f = request.FILES["file"]
        text = io.TextIOWrapper(f.file, encoding="utf-8-sig")
        reader = csv.reader(text)
        rows = list(reader)

        if not rows:
            messages.error(request, "That file has no rows.")
            return redirect("personnel:csv_import")

        header = rows[0]
        course_names = [h.strip() for h in header[5:] if h.strip()]
        course_map = {}
        for name in course_names:
            c, _ = TrainingCourse.objects.get_or_create(course_name=name)
            course_map[name] = c

        next_synthetic_id = 9000

        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            dept = row[2].strip() if len(row) > 2 else ""
            if scope.locked:
                dept = scope.name                       # a Level 1 / 2 import only ever adds to their own department
            position = row[3].strip() if len(row) > 3 else ""
            status = row[4].strip() if len(row) > 4 else "Active"
            if status not in ("Active", "Inactive"):
                status = "Active"

            person = (_people(request) if scope.locked else Personnel.objects).filter(name__iexact=name).first()
            if scope.locked and not person and Personnel.objects.filter(name__iexact=name).exists():
                results["flagged"].append(f"{name} — belongs to another department, skipped.")
                continue
            if person:
                person.department = dept or person.department
                person.position = position or person.position
                person.status = status
                person.save()
                results["updated"] += 1
            else:
                while Personnel.objects.filter(employee_id=str(next_synthetic_id)).exists():
                    next_synthetic_id += 1
                person = Personnel.objects.create(
                    employee_id=str(next_synthetic_id), name=name,
                    department=dept, position=position, status=status,
                )
                results["flagged"].append(f"{name} — created with auto-generated employee ID {next_synthetic_id}, assign a real one.")
                results["created"] += 1

            required = []
            for i, course_name in enumerate(course_names):
                col_index = 5 + i
                if col_index >= len(row):
                    continue
                value = row[col_index].strip()
                course = course_map[course_name]

                if not value or value.upper() == "MISSING":
                    PersonnelCert.objects.filter(personnel=person, course=course).delete()
                    required.append(course)
                    continue

                if value.upper() == "N/A":
                    PersonnelCert.objects.filter(personnel=person, course=course).delete()
                    continue

                try:
                    expiry = datetime.datetime.strptime(value, "%Y-%m-%d").date()
                except ValueError:
                    results["flagged"].append(f"{name} / {course_name}: couldn't parse '{value}' as a date, skipped.")
                    continue

                PersonnelCert.objects.update_or_create(
                    personnel=person, course=course,
                    defaults={"expiry_date": expiry, "notes": ""},
                )
                required.append(course)
                results["certs"] += 1

            person.required_training_courses.set(required)

        updated_count = results["updated"]
        created_count = results["created"]
        certs_count = results["certs"]
        messages.success(
            request,
            f"Updated {updated_count} people, created {created_count} new, "
            f"set {certs_count} certificate records.",
        )
        return render(request, "personnel/csv_import_results.html", {"results": results})

    return render(request, "personnel/csv_import.html", {})
