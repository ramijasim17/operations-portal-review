from django import template
from django.contrib.contenttypes.models import ContentType

from approvals.models import Approval
from approvals.registry import KINDS

register = template.Library()


@register.simple_tag
def approval_of(obj, kind):
    """The Approval row for obj/kind or None. Uses the prefetched `approvals` when the view fetched them."""
    cached = getattr(obj, "_prefetched_objects_cache", {}).get("approvals")
    rows = cached if cached is not None else obj.approvals.all()
    for row in rows:
        if row.kind == kind:
            return row
    return None


@register.inclusion_tag("approvals/_badge.html")
def approval_badge(obj, kind="", removal_kind=""):
    """A small pill: Pending approval / Rejected / Removal pending, nothing when all is well."""
    shown = []
    approval = approval_of(obj, kind) if kind else None
    if approval and approval.status != Approval.APPROVED:
        shown.append((approval.status, KINDS[kind].label, approval.note))
    removal = approval_of(obj, removal_kind) if removal_kind else None
    if removal:
        shown.append(("removal-" + removal.status, "Removal", removal.note))
    return {"items": shown}


@register.filter
def is_pending(obj, kind):
    row = approval_of(obj, kind)
    return bool(row and row.status != Approval.APPROVED)


@register.inclusion_tag("approvals/_panel.html", takes_context=True)
def approval_panel(context, obj, kind, show_ok=False, offer_submit=False):
    """A banner for a record's approval state, with the Approve / Reject / Send again buttons that fit the viewer."""
    approval = approval_of(obj, kind)
    request = context.get("request")
    profile = getattr(getattr(request, "user", None), "profile", None)
    return {
        "approval": approval, "kind": KINDS[kind], "kind_key": kind, "obj": obj, "show_ok": show_ok, "offer_submit": offer_submit,
        "can_decide": bool(profile and profile.can_approve), "can_edit": bool(profile and profile.can_edit),
        "next": request.get_full_path() if request else "",
        "csrf_token": context.get("csrf_token"),
    }
