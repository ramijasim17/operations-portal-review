from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import Approval, ApprovalLog
from .registry import KINDS, ApprovalError


def _rows(approvals):
    rows = []
    for a in approvals:
        target = a.target
        if target is None:
            continue
        kind = KINDS[a.kind]
        rows.append({"approval": a, "kind": kind, "url": kind.url(target)})
    return rows


def inbox(request):
    """Level 3: everything waiting for a decision. Everyone: their own requests and what happened to them."""
    can_decide = request.user.profile.can_approve
    waiting = []
    if can_decide:
        waiting = _rows(Approval.objects.filter(status=Approval.PENDING).select_related("submitted_by").prefetch_related("target"))
    mine = _rows(Approval.objects.filter(submitted_by=request.user).exclude(status=Approval.APPROVED)
                 .select_related("decided_by").prefetch_related("target"))
    history = list(ApprovalLog.objects.select_related("by")[:40])
    by_kind = {}
    for row in waiting:
        by_kind.setdefault(row["kind"].label, 0)
        by_kind[row["kind"].label] += 1
    return render(request, "approvals/inbox.html", {
        "can_decide": can_decide, "waiting": waiting, "mine": mine, "history": history, "by_kind": by_kind,
        "tab": request.GET.get("tab") or ("waiting" if can_decide else "mine"),
        "rejected_mine": sum(1 for r in mine if r["approval"].status == Approval.REJECTED),
    })


@require_POST
def decide(request, pk):
    approval = get_object_or_404(Approval, pk=pk, status=Approval.PENDING)
    approve = request.POST.get("decision") == "approve"
    note = (request.POST.get("note") or "").strip()
    label = approval.label
    try:
        services.decide(approval, request.user, approve=approve, note=note)
    except ApprovalError as e:
        messages.error(request, f"{label}: {e}")
        return redirect("approvals:inbox")
    messages.success(request, f"{label}: {'approved' if approve else 'rejected'}.")
    return redirect("approvals:inbox")


@require_POST
def bulk(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    done = failed = 0
    for approval in Approval.objects.filter(pk__in=ids, status=Approval.PENDING):
        try:
            services.decide(approval, request.user, approve=True)
            done += 1
        except ApprovalError:
            failed += 1
    if done:
        messages.success(request, f"{done} approved.")
    if failed:
        messages.error(request, f"{failed} could not be approved (still in use).")
    if not done and not failed:
        messages.info(request, "Nothing was selected.")
    return redirect("approvals:inbox")


@require_POST
def resubmit(request, pk):
    approval = get_object_or_404(Approval, pk=pk, status=Approval.REJECTED)
    target = approval.target
    if target is None:
        raise Http404("It no longer exists")
    services.submit(target, approval.kind, request.user)
    messages.success(request, f"{approval.label}: sent again for approval.")
    return redirect("approvals:inbox")


@require_POST
def submit_sgs(request, pk, kind):
    """Level 2 sends an SGS result or final report for approval (a Level 3 approves it on the spot)."""
    from sgs_report.models import SGSReport

    if kind not in ("sgs_result", "sgs_final"):
        raise Http404("Unknown approval")
    report = get_object_or_404(SGSReport, pk=pk)
    approval = services.submit(report, kind, request.user)
    word = "approved" if approval and approval.status == Approval.APPROVED else "sent for approval"
    messages.success(request, f"{KINDS[kind].label} for {report.well_name} {word}.")
    return redirect(f"/sgs-report/{pk}/?tab={'result' if kind == 'sgs_result' else 'final'}")
