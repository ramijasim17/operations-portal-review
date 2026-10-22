"""Audit templates (parts and the points to check) and carrying out an audit from one."""
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from action_tracker.models import Action

from .forms import AuditPartForm, AuditPointForm, AuditTemplateForm
from core.dept_scope import visible_or_404
from .scope import filter_qs, get_scope, initial_department
from .models import AuditPart, AuditPoint, AuditTemplate, Inspection, InspectionAnswer

CRITICAL_FAIL_SCORE = 50          # below this, an audit fails even with no critical point failing


# ───────── the template builder ─────────
def template_list(request):
    scope = get_scope(request)
    templates = filter_qs(AuditTemplate.objects.prefetch_related("parts__points").select_related("department"), scope, include_shared=True)
    return render(request, "qhse/template_list.html", {"section": "templates", "scope": scope, "templates": list(templates)})


def template_create(request):
    from .scope import get_scope as _scope

    scope = _scope(request)
    form = AuditTemplateForm(request.POST or None, initial={"department": scope.department if scope.mode == "dept" else None})
    if request.method == "POST" and form.is_valid():
        t = form.save()
        messages.success(request, "Template created. Add its parts and the points to check.")
        return redirect("qhse:template_detail", pk=t.pk)
    return render(request, "qhse/template_form.html", {"form": form})


def template_edit(request, pk):
    t = get_object_or_404(AuditTemplate, pk=pk)
    visible_or_404(request, t.department_id, shared=True)
    form = AuditTemplateForm(request.POST or None, instance=t)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Template updated.")
        return redirect("qhse:template_detail", pk=pk)
    return render(request, "qhse/template_form.html", {"form": form, "t": t})


def template_detail(request, pk):
    t = get_object_or_404(AuditTemplate.objects.prefetch_related("parts__points"), pk=pk)
    visible_or_404(request, t.department_id, shared=True)
    return render(request, "qhse/template_detail.html", {"section": "templates", "t": t,
                                                         "parts": list(t.parts.all()), "used": t.inspections.count()})


def template_delete(request, pk):
    t = get_object_or_404(AuditTemplate, pk=pk)
    visible_or_404(request, t.department_id, shared=True)
    if request.method == "POST":
        t.delete()
        messages.success(request, "Template deleted. Audits already done from it keep their results.")
    return redirect("qhse:template_list")


@require_POST
def template_copy(request, pk):
    t = get_object_or_404(AuditTemplate.objects.prefetch_related("parts__points"), pk=pk)
    visible_or_404(request, t.department_id, shared=True)
    name = f"{t.name} (copy)"
    n = 2
    while AuditTemplate.objects.filter(name=name).exists():
        name, n = f"{t.name} (copy {n})", n + 1
    with transaction.atomic():
        new = AuditTemplate.objects.create(name=name, kind=t.kind, description=t.description, pass_mark=t.pass_mark, is_active=t.is_active)
        for part in t.parts.all():
            p2 = AuditPart.objects.create(template=new, title=part.title, order=part.order)
            for pt in part.points.all():
                AuditPoint.objects.create(part=p2, text=pt.text, guidance=pt.guidance, answer_type=pt.answer_type, unit=pt.unit,
                                          min_value=pt.min_value, max_value=pt.max_value, critical=pt.critical,
                                          note_required=pt.note_required, order=pt.order)
    messages.success(request, "Copied. Edit the copy as you need.")
    return redirect("qhse:template_detail", pk=new.pk)


def _next_order(qs):
    return (qs.aggregate(m=Max("order"))["m"] or 0) + 1


def part_create(request, pk):
    t = get_object_or_404(AuditTemplate, pk=pk)
    visible_or_404(request, t.department_id, shared=True)
    form = AuditPartForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        part = form.save(commit=False)
        part.template, part.order = t, _next_order(t.parts)
        part.save()
        return redirect("qhse:template_detail", pk=pk)
    return render(request, "qhse/template_form.html", {"form": form, "t": t, "heading": "Add a part"})


def part_edit(request, pk):
    part = get_object_or_404(AuditPart, pk=pk)
    visible_or_404(request, part.template.department_id, shared=True)
    form = AuditPartForm(request.POST or None, instance=part)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("qhse:template_detail", pk=part.template_id)
    return render(request, "qhse/template_form.html", {"form": form, "t": part.template, "heading": "Rename part"})


def part_delete(request, pk):
    part = get_object_or_404(AuditPart, pk=pk)
    visible_or_404(request, part.template.department_id, shared=True)
    tid = part.template_id
    if request.method == "POST":
        part.delete()
    return redirect("qhse:template_detail", pk=tid)


def point_create(request, pk):
    part = get_object_or_404(AuditPart, pk=pk)
    visible_or_404(request, part.template.department_id, shared=True)
    form = AuditPointForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        point = form.save(commit=False)
        point.part, point.order = part, _next_order(part.points)
        point.save()
        messages.success(request, "Point added.")
        return redirect("qhse:template_detail", pk=part.template_id)
    return render(request, "qhse/point_form.html", {"form": form, "part": part})


def point_edit(request, pk):
    point = get_object_or_404(AuditPoint, pk=pk)
    visible_or_404(request, point.part.template.department_id, shared=True)
    form = AuditPointForm(request.POST or None, instance=point)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("qhse:template_detail", pk=point.part.template_id)
    return render(request, "qhse/point_form.html", {"form": form, "part": point.part, "point": point})


def point_delete(request, pk):
    point = get_object_or_404(AuditPoint, pk=pk)
    visible_or_404(request, point.part.template.department_id, shared=True)
    tid = point.part.template_id
    if request.method == "POST":
        point.delete()
    return redirect("qhse:template_detail", pk=tid)


@require_POST
def move(request, kind, pk):
    """Swap an item with its neighbour: kind is 'part' or 'point', direction comes from the button."""
    direction = request.POST.get("direction")
    if kind == "part":
        obj = get_object_or_404(AuditPart, pk=pk)
        visible_or_404(request, obj.template.department_id, shared=True)
        siblings = list(obj.template.parts.all())
        tid = obj.template_id
    else:
        obj = get_object_or_404(AuditPoint, pk=pk)
        visible_or_404(request, obj.part.template.department_id, shared=True)
        siblings = list(obj.part.points.all())
        tid = obj.part.template_id
    i = siblings.index(obj)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(siblings):
        siblings[i], siblings[j] = siblings[j], siblings[i]
        for n, item in enumerate(siblings, start=1):
            if item.order != n:
                item.order = n
                item.save(update_fields=["order"])
    return redirect("qhse:template_detail", pk=tid)


# ───────── carrying out an audit ─────────
def _snapshot(inspection):
    """Copy the template's points onto the audit the first time it is opened."""
    if inspection.answers.exists() or not inspection.template_id:
        return
    n = 0
    for part in inspection.template.parts.all():
        for pt in part.points.all():
            n += 1
            InspectionAnswer.objects.create(
                inspection=inspection, point=pt, part_title=part.title, point_text=pt.text, guidance=pt.guidance,
                answer_type=pt.answer_type, unit=pt.unit, min_value=pt.min_value, max_value=pt.max_value,
                critical=pt.critical, note_required=pt.note_required, order=n)


def _decimal(text):
    try:
        return Decimal(text.replace(",", ".").strip()) if text and text.strip() else None
    except InvalidOperation:
        return None


def _verdict(answers, template):
    scored = [a for a in answers if a.outcome in (InspectionAnswer.COMPLIANT, InspectionAnswer.NON_COMPLIANT)]
    good = sum(a.outcome == InspectionAnswer.COMPLIANT for a in scored)
    score = round(100 * good / len(scored)) if scored else 100
    critical = any(a.critical and a.outcome == InspectionAnswer.NON_COMPLIANT for a in answers)
    if critical or score < CRITICAL_FAIL_SCORE:
        result = Inspection.FAIL
    elif score >= template.pass_mark:
        result = Inspection.PASS
    else:
        result = Inspection.IMPROVE
    return score, result, critical


def inspection_conduct(request, pk):
    inspection = get_object_or_404(Inspection.objects.select_related("template"), pk=pk)
    visible_or_404(request, inspection.department_id)
    if inspection.is_done or not inspection.template_id:
        return redirect("qhse:inspection_detail", pk=pk)
    _snapshot(inspection)
    answers = list(inspection.answers.all())
    errors = {}
    if request.method == "POST":
        for a in answers:
            if a.answer_type == "number":
                a.value = _decimal(request.POST.get(f"value_{a.pk}", ""))
                a.outcome = a.judge_value() if a.value is not None else ""
            else:
                a.outcome = request.POST.get(f"outcome_{a.pk}", "")
                if a.outcome not in dict(InspectionAnswer.OUTCOME_CHOICES):
                    a.outcome = ""
            a.notes = request.POST.get(f"notes_{a.pk}", "").strip()
            upload = request.FILES.get(f"file_{a.pk}")
            if upload:
                a.file = upload
        finishing = request.POST.get("finish") == "1"
        if finishing:
            for a in answers:
                if not a.outcome:
                    errors[a.pk] = "Answer this point, or mark it N/A."
                elif a.outcome == InspectionAnswer.NON_COMPLIANT and a.note_required and not a.notes:
                    errors[a.pk] = "Write what was wrong."
        done_text = request.POST.get("done_date", "")
        try:
            done_date = datetime.date.fromisoformat(done_text) if done_text else timezone.localdate()
        except ValueError:
            done_date = timezone.localdate()
            errors["date"] = "Enter a valid date."
        if not errors:
            with transaction.atomic():
                for a in answers:
                    a.save()
                inspection.inspector = request.POST.get("inspector", inspection.inspector)
                inspection.auditor_company = request.POST.get("auditor_company", inspection.auditor_company)
                inspection.save()
                if not finishing:
                    messages.success(request, "Progress saved. Come back to finish it.")
                    return redirect("qhse:inspection_conduct", pk=pk)
                score, result, critical = _verdict(answers, inspection.template)
                failed = [a for a in answers if a.outcome == InspectionAnswer.NON_COMPLIANT]
                summary = f"Score {score}% ({sum(a.outcome == 'compliant' for a in answers)} compliant, {len(failed)} non-compliant)."
                if critical:
                    summary += " A critical point failed."
                inspection.score = score
                nxt = inspection.complete(done_date, result, summary + ("\n" + inspection.findings if inspection.findings else ""), inspection.inspector)
                if request.POST.get("raise_actions"):
                    for a in failed:
                        action = Action.objects.create(
                            department_id=inspection.department_id,
                            action_item=f"[{inspection.reference}] {a.point_text}"[:255], priority="High" if a.critical else "Medium",
                            action_type="equipment" if inspection.asset_serial else "procedure", raised_date=timezone.localdate(),
                            due_date=done_date + datetime.timedelta(days=14),
                            comments=f"Non-compliance found in {inspection.reference} ({a.part_title}). {a.notes}".strip())
                        inspection.actions.add(action)
                        a.action = action
                        a.save(update_fields=["action"])
            msg = f"{inspection.reference} finished: {result}, score {score}%."
            if nxt:
                msg += f" Next one scheduled for {nxt.due_date:%d %b %Y}."
            (messages.warning if result != Inspection.PASS else messages.success)(request, msg)
            return redirect("qhse:inspection_detail", pk=pk)
    parts = []
    for a in answers:
        if not parts or parts[-1]["title"] != a.part_title:
            parts.append({"title": a.part_title, "rows": []})
        parts[-1]["rows"].append(a)
    return render(request, "qhse/inspection_conduct.html", {
        "section": "inspections", "item": inspection, "parts": parts, "errors": errors, "today": timezone.localdate(),
        "answered": sum(bool(a.outcome) for a in answers), "total": len(answers),
    })
