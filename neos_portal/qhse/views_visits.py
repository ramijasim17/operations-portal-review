"""Site visit reports: draft, add observations and findings, submit for approval, then locked."""
import io

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from action_tracker.models import Action
from approvals import services as approvals

from .forms import CorrectiveActionForm, SiteVisitForm, SiteVisitItemForm
from .models import SiteVisit, SiteVisitItem
from core.dept_scope import visible_or_404
from .scope import filter_qs, get_scope, initial_department

TABS = ("all", "Draft", "Submitted", "Approved", "Rejected")


def visit_list(request):
    tab = request.GET.get("tab", "all")
    if tab not in TABS:
        tab = "all"
    q = request.GET.get("q", "").strip().lower()
    scope = get_scope(request)
    visits = list(filter_qs(SiteVisit.objects.prefetch_related("items", "approvals").select_related("department"), scope))
    counts = {t: sum(v.state == t for v in visits) for t in TABS[1:]}
    counts["all"] = len(visits)
    rows = [v for v in visits if tab == "all" or v.state == tab]
    if q:
        rows = [v for v in rows if q in v.site.lower() or q in v.reference.lower() or q in v.client.lower() or q in v.purpose.lower()]
    return render(request, "qhse/visit_list.html", {"section": "visits", "scope": scope, "visits": rows, "counts": counts, "tab": tab, "tabs": TABS, "q": q})


def visit_create(request):
    form = SiteVisitForm(request.POST or None, initial={"department": initial_department(request)}, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        visit = form.save(commit=False)
        visit.created_by = request.user
        visit.save()
        messages.success(request, f"{visit.reference} started as a draft. Add what you saw.")
        return redirect("qhse:visit_detail", pk=visit.pk)
    return render(request, "qhse/visit_form.html", {"form": form})


def _editable(request, visit):
    if visit.is_locked:
        messages.error(request, f"{visit.reference} is {visit.state.lower()} and can't be changed.")
        return False
    return True


def visit_edit(request, pk):
    visit = get_object_or_404(SiteVisit, pk=pk)
    visible_or_404(request, visit.department_id)
    if not _editable(request, visit):
        return redirect("qhse:visit_detail", pk=pk)
    form = SiteVisitForm(request.POST or None, instance=visit, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Report updated.")
        return redirect("qhse:visit_detail", pk=pk)
    return render(request, "qhse/visit_form.html", {"form": form, "visit": visit})


def visit_detail(request, pk):
    visit = get_object_or_404(SiteVisit.objects.prefetch_related("items", "approvals"), pk=pk)
    visible_or_404(request, visit.department_id)
    items = list(visit.items.all())
    return render(request, "qhse/visit_detail.html", {
        "section": "visits", "visit": visit, "items": items,
        "findings": sum(i.kind == SiteVisitItem.FINDING for i in items),
        "unactioned": sum(i.kind == SiteVisitItem.FINDING and not i.action_id for i in items),
    })


@require_POST
def visit_submit(request, pk):
    visit = get_object_or_404(SiteVisit, pk=pk)
    visible_or_404(request, visit.department_id)
    if visit.is_locked:
        return redirect("qhse:visit_detail", pk=pk)
    if not visit.items.exists() and not visit.summary.strip():
        messages.error(request, "Add a summary or at least one observation before submitting.")
        return redirect("qhse:visit_detail", pk=pk)
    approvals.submit(visit, "site_visit", request.user)
    if visit.state == "Approved":
        messages.success(request, f"{visit.reference} approved and locked.")
    else:
        messages.success(request, f"{visit.reference} sent to a Level 3 for approval. It is locked until they decide.")
    return redirect("qhse:visit_detail", pk=pk)


def visit_delete(request, pk):
    visit = get_object_or_404(SiteVisit, pk=pk)
    visible_or_404(request, visit.department_id)
    if request.method == "POST":
        visit.delete()
        messages.success(request, "Deleted.")
    return redirect("qhse:visit_list")


def item_create(request, pk):
    visit = get_object_or_404(SiteVisit, pk=pk)
    visible_or_404(request, visit.department_id)
    if not _editable(request, visit):
        return redirect("qhse:visit_detail", pk=pk)
    form = SiteVisitItemForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.visit, item.order = visit, (visit.items.count() + 1)
        item.save()
        return redirect("qhse:visit_detail", pk=pk)
    return render(request, "qhse/visit_item_form.html", {"form": form, "visit": visit})


def item_edit(request, pk):
    item = get_object_or_404(SiteVisitItem.objects.select_related("visit"), pk=pk)
    visible_or_404(request, item.visit.department_id)
    if not _editable(request, item.visit):
        return redirect("qhse:visit_detail", pk=item.visit_id)
    form = SiteVisitItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("qhse:visit_detail", pk=item.visit_id)
    return render(request, "qhse/visit_item_form.html", {"form": form, "visit": item.visit, "item": item})


def item_remove(request, pk):
    item = get_object_or_404(SiteVisitItem.objects.select_related("visit"), pk=pk)
    visible_or_404(request, item.visit.department_id)
    vid = item.visit_id
    if request.method == "POST" and _editable(request, item.visit):
        item.delete()
    return redirect("qhse:visit_detail", pk=vid)


def item_raise_action(request, pk):
    item = get_object_or_404(SiteVisitItem.objects.select_related("visit"), pk=pk)
    visible_or_404(request, item.visit.department_id)
    visit = item.visit
    form = CorrectiveActionForm(request.POST or None, initial={
        "action_item": f"[{visit.reference}] {item.text[:150]}", "priority": item.severity if item.severity in ("High", "Medium", "Low") else ("High" if item.severity == "Critical" else "Medium")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        action = Action.objects.create(
            department_id=visit.department_id,
            action_item=d["action_item"], assignee=d["assignee"], priority=d["priority"], due_date=d["due_date"], client=visit.client,
            action_type="procedure", raised_date=timezone.localdate(),
            comments=f"From site visit {visit.reference} at {visit.site}, {item.area or 'general'}: {item.text}")
        item.action = action
        item.save(update_fields=["action"])
        messages.success(request, "Action created. Follow it up in the Action Tracker.")
        return redirect("qhse:visit_detail", pk=visit.pk)
    return render(request, "qhse/action_form.html", {"form": form, "obj": visit, "back": "qhse:visit_detail"})


def visit_pdf(request, pk):
    from .visit_pdf import build_visit_pdf

    visit = get_object_or_404(SiteVisit.objects.prefetch_related("items", "approvals"), pk=pk)

    visible_or_404(request, visit.department_id)
    data = build_visit_pdf(visit)
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(True, f"{visit.reference}.pdf")
    return response
