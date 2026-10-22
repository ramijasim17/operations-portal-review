"""Bell notifications for approvals: approvers hear about new requests, submitters about decisions."""
from django.contrib.auth import get_user_model

from notifications.models import Notification

from .registry import KINDS


def _who(user):
    return (user.get_full_name() or user.username) if user else "Someone"


def approvers(exclude=None):
    User = get_user_model()
    qs = User.objects.filter(profile__role__in=("approver", "admin"), profile__is_active_employee=True, is_active=True)
    return qs.exclude(pk=exclude.pk) if exclude else qs


def submitted(approval):
    kind = KINDS[approval.kind]
    verb = "asked to remove" if kind.removal else "submitted"
    text = f"{_who(approval.submitted_by)} {verb} {kind.label.lower()}: {approval.label} — waiting for your approval"
    for user in approvers(exclude=approval.submitted_by):
        Notification.objects.create(recipient=user, actor=approval.submitted_by, kind=Notification.APPROVAL,
                                    message=text[:400], link="/approvals/")


def decided(approval, user, approved, note=""):
    if not approval.submitted_by or approval.submitted_by_id == getattr(user, "pk", None):
        return
    kind = KINDS[approval.kind]
    word = "approved" if approved else "rejected"
    text = f"{_who(user)} {word} {kind.label.lower()}: {approval.label}" + (f" — {note}" if note else "")
    Notification.objects.create(recipient=approval.submitted_by, actor=user, kind=Notification.APPROVAL,
                                message=text[:400], link="/approvals/")
