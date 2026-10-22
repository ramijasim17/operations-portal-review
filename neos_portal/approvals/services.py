"""Submitting work for approval, deciding on it, and asking what state something is in."""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from .models import Approval, ApprovalLog
from .registry import KINDS, ApprovalError


def needs_approval(user):
    """Level 1 / Level 2 work waits for a decision; Level 3 and admins are approved on the spot."""
    profile = getattr(user, "profile", None)
    return bool(profile) and not profile.can_approve


def _ct(obj):
    return ContentType.objects.get_for_model(obj, for_concrete_model=False)


def _log(obj, kind, action, by, note="", snapshot=None):
    """`snapshot` = (content type, id, label) taken before a removal deletes the object."""
    ct, pk, label = snapshot or (_ct(obj), obj.pk, KINDS[kind].describe(obj)[:250])
    ApprovalLog.objects.create(content_type=ct, object_id=pk, kind=kind, label=label,
                               action=action, by=by if getattr(by, "pk", None) else None, note=note[:300])


def get(obj, kind):
    return Approval.objects.filter(content_type=_ct(obj), object_id=obj.pk, kind=kind).first()


def state(obj, kind):
    """'approved' (also for anything that has no Approval row), 'pending' or 'rejected'."""
    approval = get(obj, kind)
    return approval.status if approval else Approval.APPROVED


def is_approved(obj, kind):
    return state(obj, kind) == Approval.APPROVED


def is_released(obj, kind):
    """For things that start as a draft (SGS result / final report): released only once someone approved it.
    Unlike is_approved(), 'no Approval row' means NOT approved here."""
    approval = get(obj, kind)
    return bool(approval and approval.status == Approval.APPROVED)


@transaction.atomic
def submit(obj, kind, user, note=""):
    """Put `obj` forward for approval. A Level 3 / admin submitter approves it at once."""
    from . import notices

    label = KINDS[kind].describe(obj)[:250]
    approval, created = Approval.objects.get_or_create(
        content_type=_ct(obj), object_id=obj.pk, kind=kind,
        defaults={"label": label, "submitted_by": user if getattr(user, "pk", None) else None})
    if not created:
        approval.status, approval.label = Approval.PENDING, label
        approval.submitted_by = user if getattr(user, "pk", None) else None
        approval.submitted_at, approval.decided_by, approval.decided_at, approval.note, approval.auto = timezone.now(), None, None, "", False
        approval.save()
    if not needs_approval(user):
        return decide(approval, user, approve=True, auto=True)
    _log(obj, kind, "resubmitted" if not created else "submitted", user, note)
    notices.submitted(approval)
    return approval


@transaction.atomic
def decide(approval, user, approve, note="", auto=False):
    """Approve or reject. Approving a removal carries the removal out."""
    from . import notices

    obj = approval.target
    kind = KINDS[approval.kind]
    if obj is None:                                   # it was deleted in the meantime
        approval.delete()
        return None
    snapshot = (_ct(obj), obj.pk, kind.describe(obj)[:250])
    if approve and kind.on_approved and not auto:
        kind.on_approved(obj, user)                   # may raise ApprovalError, leaving everything as it was
    if approve and kind.removal:
        _log(obj, approval.kind, "approved", user, note, snapshot=snapshot)
        notices.decided(approval, user, True, note)
        Approval.objects.filter(pk=approval.pk).delete()       # the record is gone with it; the log keeps the story
        return None
    approval.status = Approval.APPROVED if approve else Approval.REJECTED
    approval.decided_by, approval.decided_at, approval.note, approval.auto = user, timezone.now(), note[:300], auto
    approval.save()
    if auto:
        _log(obj, approval.kind, "auto", user)
    else:
        _log(obj, approval.kind, "approved" if approve else "rejected", user, note)
        notices.decided(approval, user, approve, note)
    return approval


def pending_count():
    return Approval.objects.filter(status=Approval.PENDING).count()


def pending_pks(model, kind):
    """Primary keys of `model` rows whose `kind` approval is not (yet) approved."""
    ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
    return list(Approval.objects.filter(content_type=ct, kind=kind).exclude(status=Approval.APPROVED)
                .values_list("object_id", flat=True))


def only_approved(queryset, kind):
    """Drop the rows that are waiting for approval or were rejected."""
    blocked = pending_pks(queryset.model, kind)
    return queryset.exclude(pk__in=blocked) if blocked else queryset


def request_removal(obj, kind, user):
    """Level 2 asks for a removal; a Level 3 / admin removal is done by the caller straight away."""
    return submit(obj, kind, user)


def blocked_asset_names(names):
    """Of these Asset No / Serial No values, the ones that can't go on a manifest: assets still waiting for
    approval (or rejected), and assets with a removal request open."""
    from django.apps import apps
    from django.db.models import Q

    Asset = apps.get_model("departments", "Asset")
    ct = ContentType.objects.get_for_model(Asset)
    blocked = Approval.objects.filter(content_type=ct).filter(
        Q(kind="asset") & ~Q(status=Approval.APPROVED) | Q(kind="asset_removal", status=Approval.PENDING)
    ).values_list("object_id", flat=True)
    return set(Asset.objects.filter(name__in=list(names), pk__in=list(blocked)).values_list("name", flat=True))


@transaction.atomic
def reopen(obj, kind, user, why="changed after it was approved"):
    """A Level 1/2 person changed something that had been approved: it goes back to Pending."""
    approval = get(obj, kind)
    if approval is None or approval.status != Approval.APPROVED or not needs_approval(user):
        return None
    from . import notices

    approval.status, approval.decided_by, approval.decided_at, approval.note, approval.auto = Approval.PENDING, None, None, "", False
    approval.submitted_by, approval.submitted_at = (user if getattr(user, "pk", None) else None), timezone.now()
    approval.save()
    _log(obj, kind, "reopened", user, why)
    notices.submitted(approval)
    return approval
