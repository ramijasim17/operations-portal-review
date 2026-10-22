"""Creating notifications: change alerts for managers, and 'this was assigned to you' alerts."""
import datetime

from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import Notification

FOLD_MINUTES = 10          # the same kind of change by the same person within this window is one alert

# model -> (url name, attribute holding the pk of the page to open)
PAGE_FOR = {
    "job_reports.jobreport": ("job_reports:detail", "pk"),
    "job_reports.jobreportdocument": ("job_reports:detail", "job_report_id"),
    "job_reports.jobsupervisor": ("job_reports:detail", "job_report_id"),
    "action_tracker.action": ("action_tracker:detail", "pk"),
    "action_tracker.actionstep": ("action_tracker:detail", "action_id"),
    "personnel.personnel": ("personnel:detail", "pk"),
    "personnel.personnelcert": ("personnel:detail", "personnel_id"),
    "job_manager.jobmanifest": ("job_manager:detail", "pk"),
    "sgs_report.sgsreport": ("sgs_report:detail", "pk"),
    "sqb.sqbpackage": ("sqb:package_detail", "pk"),
}
LIST_FOR_APP = {
    "equipment": "equipment:list", "departments": "departments:list", "maintenance": "maintenance:list",
    "wire_management": "wire_management:list", "sqb": "sqb:job_list", "document_control": "document_control:list",
    "work_approval": "work_approval:list", "job_manager": "job_manager:list", "job_reports": "job_reports:list",
    "personnel": "personnel:list", "sgs_report": "sgs_report:list", "action_tracker": "action_tracker:list",
}


def person(user):
    return (user.get_full_name() or user.username) if user else "Someone"


def page_for(instance, deleted=False):
    label = instance._meta.label_lower
    try:
        if not deleted and label in PAGE_FOR:
            name, attr = PAGE_FOR[label]
            return reverse(name, args=[getattr(instance, attr)])
        return reverse(LIST_FOR_APP[instance._meta.app_label])
    except (NoReverseMatch, KeyError, TypeError):
        return ""


def describe(instance):
    try:
        return str(instance)[:80]
    except Exception:       # a related record is already gone
        return f"#{instance.pk}"


def managers(exclude=None):
    """Active accounts that asked to hear about every change."""
    User = get_user_model()
    users = User.objects.filter(profile__notify_all=True, profile__is_active_employee=True, is_active=True)
    return users.exclude(pk=exclude.pk) if exclude else users


def _sentence(actor, verb, meta, count, label):
    if count == 1:
        return f"{person(actor)} {verb} {meta.verbose_name} “{label}”"
    return f"{person(actor)} {verb} {count} {meta.verbose_name_plural}"


def record_change(instance, verb, actor):
    """Tell every manager (except the person who did it) about a change."""
    meta = instance._meta
    label = describe(instance)
    link = page_for(instance, deleted=(verb == "deleted"))
    since = timezone.now() - datetime.timedelta(minutes=FOLD_MINUTES)
    for user in managers(exclude=actor):
        same = Notification.objects.filter(
            recipient=user, actor=actor, kind=Notification.ACTIVITY, model_label=meta.label_lower, verb=verb,
            read_at__isnull=True, updated_at__gte=since).first()
        if same:
            # Replace it with a fresh one carrying the new total, so the bell treats it as new again
            # (a quietly edited old alert would never pop up).
            count = same.count + 1
            same.delete()
            Notification.objects.create(
                recipient=user, actor=actor, kind=Notification.ACTIVITY, model_label=meta.label_lower, verb=verb, count=count,
                message=_sentence(actor, verb, meta, count, label), link=page_for_list(instance))
        else:
            Notification.objects.create(
                recipient=user, actor=actor, kind=Notification.ACTIVITY, model_label=meta.label_lower, verb=verb,
                message=_sentence(actor, verb, meta, 1, label), link=link)


def page_for_list(instance):
    try:
        return reverse(LIST_FOR_APP[instance._meta.app_label])
    except (NoReverseMatch, KeyError):
        return ""


def assigned(user, message, link, actor=None):
    """'This was given to you.' Not sent when you assign something to yourself."""
    if user is None or (actor and user.pk == actor.pk):
        return None
    return Notification.objects.create(recipient=user, actor=actor, kind=Notification.ASSIGNED, message=message, link=link)


def user_for_name(name):
    """The portal account of the employee with this name (assignees are stored as names)."""
    if not name:
        return None
    User = get_user_model()
    return User.objects.filter(profile__employee__name=name, profile__is_active_employee=True, is_active=True).first()
